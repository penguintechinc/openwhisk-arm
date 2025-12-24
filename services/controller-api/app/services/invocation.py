"""
OpenWhisk Invocation Orchestrator Service.

This service coordinates action invocations, including:
- Direct action invocations (blocking and non-blocking)
- Sequence executions (chaining multiple actions)
- Trigger-based invocations (event-driven execution)
- Activation record management
- Result tracking and retrieval

Architecture:
- Controller publishes invocation messages to Redis streams
- Invokers consume messages, execute actions, publish results
- Controller subscribes to results for blocking invocations
- Activation records track all executions

Message Flow:
1. invoke_action() -> Redis Stream (invoker queue)
2. Invoker executes action
3. Invoker publishes result -> Redis Stream (result queue)
4. Controller consumes result (if blocking)
5. Activation record updated with result
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from pydal import DAL

logger = logging.getLogger(__name__)


class InvocationService:
    """
    Orchestrate OpenWhisk action invocations.

    Manages the complete lifecycle of action invocations:
    - Loading action metadata from database
    - Validating execution limits
    - Publishing invocation messages to message queue
    - Tracking activation records
    - Handling blocking vs non-blocking execution
    - Coordinating sequence executions
    - Trigger-based invocations
    """

    def __init__(
        self,
        storage_service: Any,  # MinIO StorageService
        messaging_service: Any,  # Redis MessagingService
        db: DAL
    ) -> None:
        """
        Initialize invocation orchestrator.

        Args:
            storage_service: MinIO service for action code storage
            messaging_service: Redis service for message passing
            db: PyDAL database instance
        """
        self.storage = storage_service
        self.messaging = messaging_service
        self.db = db
        logger.info("InvocationService initialized")

    def invoke_action(
        self,
        namespace: str,
        action_name: str,
        params: dict[str, Any],
        blocking: bool = False,
        result_only: bool = False,
        timeout_ms: int = 60000,
        subject: str = "anonymous",
        cause: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Invoke an OpenWhisk action.

        Args:
            namespace: Namespace containing the action
            action_name: Name of action to invoke (may include package)
            params: Parameters to pass to action
            blocking: Wait for result before returning
            result_only: Return only result payload (requires blocking=True)
            timeout_ms: Maximum execution time in milliseconds
            subject: Email/ID of invoking user
            cause: Parent activation ID for sequences

        Returns:
            If blocking=True, result_only=False: Full activation record
            If blocking=True, result_only=True: Just the result payload
            If blocking=False: {"activationId": "..."}

        Raises:
            ValueError: Action not found or invalid parameters
            TimeoutError: Blocking invocation timed out
            RuntimeError: Internal invocation error
        """
        logger.info(
            f"Invoking action {namespace}/{action_name}, "
            f"blocking={blocking}, result_only={result_only}"
        )

        # Load action from database
        action = self._get_action(namespace, action_name)
        if not action:
            raise ValueError(
                f"Action {namespace}/{action_name} not found"
            )

        # Validate timeout against action limits
        max_timeout = action.limits_timeout
        if timeout_ms > max_timeout:
            logger.warning(
                f"Requested timeout {timeout_ms}ms exceeds limit {max_timeout}ms, "
                f"clamping to limit"
            )
            timeout_ms = max_timeout

        # Generate activation ID
        activation_id = str(uuid.uuid4())
        start_time = int(time.time() * 1000)  # Epoch milliseconds

        # Handle sequences differently
        if action.exec_kind == 'sequence':
            return self.invoke_sequence(
                namespace=namespace,
                sequence_action=action,
                params=params,
                blocking=blocking,
                subject=subject,
                cause=cause
            )

        # Get code reference from MinIO
        code_reference = self._get_code_reference(action)

        # Build invocation message
        invocation_msg = self._build_invocation_message(
            action=action,
            params=params,
            activation_id=activation_id,
            blocking=blocking,
            timeout_ms=timeout_ms,
            subject=subject,
            cause=cause,
            code_reference=code_reference
        )

        # Create activation record with 'pending' status
        self._create_activation_record(
            activation_id=activation_id,
            action=action,
            namespace=namespace,
            params=params,
            start_time=start_time,
            subject=subject,
            cause=cause
        )

        # Select invoker and publish message
        invoker_id = self._select_invoker()
        logger.info(
            f"Publishing invocation {activation_id} to invoker {invoker_id}"
        )
        self.messaging.publish_invocation(
            invoker_id=invoker_id,
            message=invocation_msg
        )

        # Handle blocking vs non-blocking
        if blocking:
            # Wait for result
            result = self._wait_for_result(
                activation_id=activation_id,
                timeout_ms=timeout_ms
            )

            # Update activation record with result
            self._update_activation_result(
                activation_id=activation_id,
                result=result
            )

            if result_only:
                # Return just the result payload
                return result.get('response', {}).get('result', {})
            else:
                # Return full activation record
                return result
        else:
            # Non-blocking: return immediately with activation ID
            return {
                "activationId": activation_id
            }

    def invoke_sequence(
        self,
        namespace: str,
        sequence_action: Any,  # Action row from database
        params: dict[str, Any],
        blocking: bool = False,
        subject: str = "anonymous",
        cause: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Execute a sequence of actions in order.

        Sequences chain multiple actions together, passing the output
        of each action as input to the next. All executions are tracked
        with a parent-child relationship via the 'cause' field.

        Args:
            namespace: Namespace containing the sequence
            sequence_action: Sequence action metadata from database
            params: Initial parameters for first action
            blocking: Wait for all actions to complete
            subject: Invoking user
            cause: Parent activation ID (if nested sequence)

        Returns:
            Result of final action in sequence

        Raises:
            ValueError: Sequence definition invalid
            RuntimeError: Action execution failed
        """
        logger.info(
            f"Executing sequence {namespace}/{sequence_action.name} "
            f"with {len(sequence_action.exec_components)} components"
        )

        # Validate sequence has components
        components = sequence_action.exec_components
        if not components or not isinstance(components, list):
            raise ValueError(
                f"Sequence {namespace}/{sequence_action.name} has no components"
            )

        # Generate parent activation ID for sequence
        sequence_activation_id = str(uuid.uuid4())

        # Execute each action in sequence
        current_params = params
        last_result = None

        for idx, component_path in enumerate(components):
            logger.info(
                f"Sequence step {idx + 1}/{len(components)}: {component_path}"
            )

            # Parse component path (may be /namespace/package/action or /namespace/action)
            component_namespace, component_action = self._parse_action_path(
                component_path
            )

            # Invoke component action (always blocking for sequences)
            try:
                result = self.invoke_action(
                    namespace=component_namespace,
                    action_name=component_action,
                    params=current_params,
                    blocking=True,
                    result_only=False,
                    subject=subject,
                    cause=sequence_activation_id
                )

                # Extract result for next action
                if result.get('response', {}).get('success'):
                    current_params = result['response'].get('result', {})
                    last_result = result
                else:
                    # Action failed, abort sequence
                    error_msg = result['response'].get('result', {}).get('error', 'Unknown error')
                    raise RuntimeError(
                        f"Sequence failed at step {idx + 1}: {error_msg}"
                    )

            except Exception as e:
                logger.error(f"Sequence execution failed at step {idx + 1}: {e}")
                raise

        # Return final result
        if blocking:
            return last_result
        else:
            return {
                "activationId": sequence_activation_id
            }

    def invoke_trigger(
        self,
        namespace: str,
        trigger_name: str,
        params: dict[str, Any],
        subject: str = "anonymous"
    ) -> list[str]:
        """
        Fire a trigger and invoke all associated rules.

        When a trigger fires, all active rules associated with that
        trigger are evaluated. Each rule invokes its action with the
        trigger event data merged with default parameters.

        Args:
            namespace: Namespace containing the trigger
            trigger_name: Name of trigger to fire
            params: Event parameters to pass to actions
            subject: Invoking user

        Returns:
            List of activation IDs for all invoked actions

        Raises:
            ValueError: Trigger not found
        """
        logger.info(f"Firing trigger {namespace}/{trigger_name}")

        # Get trigger from database
        trigger = self._get_trigger(namespace, trigger_name)
        if not trigger:
            raise ValueError(
                f"Trigger {namespace}/{trigger_name} not found"
            )

        # Merge trigger default parameters with event params
        merged_params = {**trigger.parameters, **params}

        # Find all active rules for this trigger
        rules = self.db(
            (self.db.rule.trigger_id == trigger.id) &
            (self.db.rule.status == 'active')
        ).select()

        activation_ids = []

        # Invoke each rule's action
        for rule in rules:
            try:
                # Get action from rule
                action = self.db.action[rule.action_id]
                if not action:
                    logger.warning(
                        f"Rule {rule.name} references non-existent action {rule.action_id}"
                    )
                    continue

                # Get namespace for action
                action_namespace = self.db.namespace[action.namespace_id]
                if not action_namespace:
                    logger.warning(
                        f"Action {action.name} has invalid namespace {action.namespace_id}"
                    )
                    continue

                # Invoke action (non-blocking)
                result = self.invoke_action(
                    namespace=action_namespace.name,
                    action_name=action.name,
                    params=merged_params,
                    blocking=False,
                    subject=subject
                )

                activation_ids.append(result['activationId'])
                logger.info(
                    f"Rule {rule.name} triggered action {action.name}: "
                    f"{result['activationId']}"
                )

            except Exception as e:
                logger.error(
                    f"Failed to invoke action for rule {rule.name}: {e}"
                )
                # Continue with other rules even if one fails

        logger.info(
            f"Trigger {namespace}/{trigger_name} fired {len(activation_ids)} actions"
        )
        return activation_ids

    def _get_action(self, namespace: str, action_name: str) -> Optional[Any]:
        """
        Load action from database.

        Args:
            namespace: Namespace name
            action_name: Action name (may include package)

        Returns:
            Action row or None if not found
        """
        # Get namespace
        ns = self.db(self.db.namespace.name == namespace).select().first()
        if not ns:
            return None

        # Parse package/action if present
        if '/' in action_name:
            parts = action_name.split('/')
            package_name = parts[0]
            action_name = parts[1]

            # Get package
            package = self.db(
                (self.db.package.namespace_id == ns.id) &
                (self.db.package.name == package_name)
            ).select().first()

            if not package:
                return None

            # Get action in package
            action = self.db(
                (self.db.action.namespace_id == ns.id) &
                (self.db.action.package_id == package.id) &
                (self.db.action.name == action_name)
            ).select().first()
        else:
            # Get action in default package (package_id is None)
            action = self.db(
                (self.db.action.namespace_id == ns.id) &
                (self.db.action.package_id == None) &
                (self.db.action.name == action_name)
            ).select().first()

        return action

    def _get_trigger(self, namespace: str, trigger_name: str) -> Optional[Any]:
        """
        Load trigger from database.

        Args:
            namespace: Namespace name
            trigger_name: Trigger name

        Returns:
            Trigger row or None if not found
        """
        # Get namespace
        ns = self.db(self.db.namespace.name == namespace).select().first()
        if not ns:
            return None

        # Get trigger
        trigger = self.db(
            (self.db.trigger.namespace_id == ns.id) &
            (self.db.trigger.name == trigger_name)
        ).select().first()

        return trigger

    def _get_code_reference(self, action: Any) -> dict[str, str]:
        """
        Get code storage reference for action.

        Args:
            action: Action row from database

        Returns:
            Dictionary with code storage metadata
        """
        return {
            "bucket": "actions",
            "key": f"{action.namespace_id}/{action.id}/{action.exec_code_hash}",
            "hash": action.exec_code_hash
        }

    def _build_invocation_message(
        self,
        action: Any,
        params: dict[str, Any],
        activation_id: str,
        blocking: bool,
        timeout_ms: int,
        subject: str,
        cause: Optional[str],
        code_reference: dict[str, str]
    ) -> dict[str, Any]:
        """
        Build complete invocation message for invoker.

        Args:
            action: Action metadata from database
            params: Parameters for action
            activation_id: Unique activation ID
            blocking: Whether invocation is blocking
            timeout_ms: Maximum execution time
            subject: Invoking user
            cause: Parent activation ID
            code_reference: MinIO storage reference

        Returns:
            Complete invocation message
        """
        # Get namespace for fully qualified name
        namespace = self.db.namespace[action.namespace_id]

        # Build fully qualified action name
        if action.package_id:
            package = self.db.package[action.package_id]
            fqn = f"/{namespace.name}/{package.name}/{action.name}"
        else:
            fqn = f"/{namespace.name}/{action.name}"

        message = {
            "activationId": activation_id,
            "action": {
                "name": fqn,
                "version": action.version,
                "namespace": namespace.name,
                "kind": action.exec_kind,
                "image": action.exec_image,
                "binary": action.exec_binary,
                "main": action.exec_main,
                "code": code_reference,
                "limits": {
                    "timeout": min(timeout_ms, action.limits_timeout),
                    "memory": action.limits_memory,
                    "logs": action.limits_logs,
                    "concurrency": action.limits_concurrency
                },
                "parameters": action.parameters
            },
            "params": params,
            "blocking": blocking,
            "subject": subject,
            "timestamp": int(time.time() * 1000)
        }

        if cause:
            message["cause"] = cause

        return message

    def _select_invoker(self) -> str:
        """
        Select an invoker for action execution.

        Currently uses simple random selection. Future enhancements:
        - Check invoker health from heartbeats
        - Load balancing based on queue depth
        - Affinity for warm container reuse
        - Resource-aware scheduling

        Returns:
            Invoker ID to use for invocation
        """
        # TODO: Implement invoker health checking from heartbeats
        # TODO: Implement smart load balancing

        # Simple random selection for now
        invoker_count = 3  # Default number of invokers
        invoker_id = f"invoker{random.randint(0, invoker_count - 1)}"

        logger.debug(f"Selected invoker: {invoker_id}")
        return invoker_id

    def _create_activation_record(
        self,
        activation_id: str,
        action: Any,
        namespace: str,
        params: dict[str, Any],
        start_time: int,
        subject: str,
        cause: Optional[str]
    ) -> None:
        """
        Create initial activation record with 'pending' status.

        Args:
            activation_id: Unique activation ID
            action: Action metadata
            namespace: Namespace name
            params: Action parameters
            start_time: Start time in epoch milliseconds
            subject: Invoking user
            cause: Parent activation ID
        """
        # Build fully qualified action name
        ns = self.db.namespace[action.namespace_id]
        if action.package_id:
            package = self.db.package[action.package_id]
            fqn = f"/{ns.name}/{package.name}/{action.name}"
        else:
            fqn = f"/{ns.name}/{action.name}"

        # Create activation record
        self.db.activation.insert(
            activation_id=activation_id,
            namespace_id=action.namespace_id,
            action_name=fqn,
            action_version=action.version,
            subject=subject,
            start=start_time,
            end=None,
            duration=None,
            status_code=0,
            response_success=True,
            response_result=None,
            logs=[],
            annotations={
                "path": fqn,
                "kind": action.exec_kind,
                "limits": {
                    "timeout": action.limits_timeout,
                    "memory": action.limits_memory,
                    "logs": action.limits_logs,
                    "concurrency": action.limits_concurrency
                }
            },
            cause=cause,
            publish=action.publish
        )
        self.db.commit()

        logger.info(f"Created activation record: {activation_id}")

    def _wait_for_result(
        self,
        activation_id: str,
        timeout_ms: int
    ) -> dict[str, Any]:
        """
        Wait for activation result from invoker.

        Subscribes to result stream and waits for completion message.

        Args:
            activation_id: Activation to wait for
            timeout_ms: Maximum wait time in milliseconds

        Returns:
            Activation result

        Raises:
            TimeoutError: Result not received within timeout
        """
        logger.info(
            f"Waiting for result of activation {activation_id}, "
            f"timeout={timeout_ms}ms"
        )

        # TODO: Implement result subscription via Redis
        # For now, poll database for result
        start = time.time()
        timeout_sec = timeout_ms / 1000.0

        while (time.time() - start) < timeout_sec:
            # Check if activation has result
            activation = self.db(
                self.db.activation.activation_id == activation_id
            ).select().first()

            if activation and activation.end is not None:
                # Activation completed, return result
                return self._activation_to_dict(activation)

            # Sleep before retry
            time.sleep(0.1)

        # Timeout exceeded
        raise TimeoutError(
            f"Activation {activation_id} timed out after {timeout_ms}ms"
        )

    def _update_activation_result(
        self,
        activation_id: str,
        result: dict[str, Any]
    ) -> None:
        """
        Update activation record with execution result.

        Args:
            activation_id: Activation to update
            result: Result data from invoker
        """
        # Find activation
        activation = self.db(
            self.db.activation.activation_id == activation_id
        ).select().first()

        if not activation:
            logger.error(
                f"Cannot update activation {activation_id}: not found"
            )
            return

        # Update with result
        activation.update_record(
            end=result.get('end'),
            duration=result.get('duration'),
            status_code=result.get('statusCode', 0),
            response_success=result.get('response', {}).get('success', True),
            response_result=result.get('response', {}).get('result'),
            logs=result.get('logs', []),
            annotations={
                **activation.annotations,
                **result.get('annotations', {})
            }
        )
        self.db.commit()

        logger.info(f"Updated activation {activation_id} with result")

    def _activation_to_dict(self, activation: Any) -> dict[str, Any]:
        """
        Convert activation record to API response format.

        Args:
            activation: Activation row from database

        Returns:
            Dictionary matching OpenWhisk activation format
        """
        return {
            "activationId": activation.activation_id,
            "namespace": self.db.namespace[activation.namespace_id].name,
            "name": activation.action_name,
            "version": activation.action_version,
            "subject": activation.subject,
            "start": activation.start,
            "end": activation.end,
            "duration": activation.duration,
            "statusCode": activation.status_code,
            "response": {
                "success": activation.response_success,
                "result": activation.response_result or {}
            },
            "logs": activation.logs,
            "annotations": activation.annotations,
            "cause": activation.cause,
            "publish": activation.publish
        }

    def _parse_action_path(self, action_path: str) -> tuple[str, str]:
        """
        Parse fully qualified action path into namespace and action name.

        Args:
            action_path: Path like /namespace/action or /namespace/package/action

        Returns:
            Tuple of (namespace, action_name)

        Raises:
            ValueError: Invalid action path format
        """
        parts = action_path.strip('/').split('/')

        if len(parts) == 2:
            # /namespace/action
            return parts[0], parts[1]
        elif len(parts) == 3:
            # /namespace/package/action
            return parts[0], f"{parts[1]}/{parts[2]}"
        else:
            raise ValueError(
                f"Invalid action path format: {action_path}"
            )
