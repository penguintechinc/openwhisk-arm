"""Redis Streams messaging service for Controller-Invoker communication.

This module provides the MessagingService class for publishing and consuming
messages between the Controller and Invoker components using Redis Streams.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import redis
from redis.exceptions import RedisError, ResponseError

logger = logging.getLogger(__name__)


@dataclass
class InvocationMessage:
    """Message for invoking an action."""

    activation_id: str
    action: str
    params: Dict[str, Any]
    blocking: bool
    response_channel: str
    deadline: int
    namespace: Optional[str] = None
    auth_key: Optional[str] = None


@dataclass
class ActivationMessage:
    """Message for activation result."""

    activation_id: str
    status_code: int
    response: Dict[str, Any]
    logs: List[str]
    duration: int
    invoker_id: Optional[str] = None


@dataclass
class HeartbeatMessage:
    """Message for invoker health heartbeat."""

    invoker_id: str
    timestamp: int
    capacity: int
    active_containers: int
    status: str


class MessagingService:
    """Redis Streams-based messaging service for Controller-Invoker communication."""

    # Stream names
    INVOCATIONS_STREAM = "penguinwhisk:invocations"
    ACTIVATIONS_STREAM = "penguinwhisk:activations"
    HEARTBEATS_STREAM = "penguinwhisk:heartbeats"

    def __init__(self, redis_url: Optional[str] = None):
        """Initialize the messaging service.

        Args:
            redis_url: Redis connection URL (redis://host:port/db)
                      Defaults to REDIS_URL environment variable
        """
        self.redis_url = redis_url or os.getenv(
            "REDIS_URL", "redis://localhost:6379/0"
        )
        self.max_stream_len = int(os.getenv("REDIS_STREAM_MAX_LEN", "10000"))

        try:
            self.redis_client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Test connection
            self.redis_client.ping()
            logger.info(f"Connected to Redis at {self.redis_url}")
        except RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

        # Initialize consumer groups
        self._setup_consumer_groups()

    def _setup_consumer_groups(self) -> None:
        """Setup consumer groups for streams."""
        streams = [
            (self.INVOCATIONS_STREAM, "invokers"),
            (self.ACTIVATIONS_STREAM, "controllers"),
            (self.HEARTBEATS_STREAM, "monitors"),
        ]

        for stream_name, group_name in streams:
            try:
                self.create_consumer_group(stream_name, group_name)
            except Exception as e:
                logger.warning(
                    f"Consumer group setup for {stream_name}:{group_name} failed: {e}"
                )

    def publish_invocation(self, invocation_request: InvocationMessage) -> str:
        """Publish an invocation request to the invocations stream.

        Args:
            invocation_request: InvocationMessage with action details

        Returns:
            str: Message ID from Redis XADD

        Raises:
            RedisError: If publishing fails
        """
        try:
            message_data = asdict(invocation_request)
            # Convert params to JSON string for Redis
            message_data["params"] = json.dumps(message_data["params"])

            message_id = self.redis_client.xadd(
                self.INVOCATIONS_STREAM,
                message_data,
                maxlen=self.max_stream_len,
                approximate=True,
            )

            logger.info(
                f"Published invocation {invocation_request.activation_id} "
                f"with message ID {message_id}"
            )
            return message_id

        except RedisError as e:
            logger.error(f"Failed to publish invocation: {e}")
            raise

    def subscribe_activation(
        self, activation_id: str, timeout_ms: int = 30000
    ) -> Optional[Dict[str, Any]]:
        """Wait for a specific activation result (blocking).

        Args:
            activation_id: The activation ID to wait for
            timeout_ms: Timeout in milliseconds (default 30s)

        Returns:
            dict: Activation record if found, None on timeout
        """
        try:
            # Use XREAD with BLOCK to wait for new messages
            # Start from '0' to check existing messages first
            last_id = "0"
            start_time = self.redis_client.time()[0]
            timeout_seconds = timeout_ms / 1000.0

            while True:
                # Calculate remaining timeout
                elapsed = self.redis_client.time()[0] - start_time
                remaining_ms = max(0, int((timeout_seconds - elapsed) * 1000))

                if remaining_ms == 0:
                    logger.debug(
                        f"Timeout waiting for activation {activation_id}"
                    )
                    return None

                # Read messages with blocking
                messages = self.redis_client.xread(
                    {self.ACTIVATIONS_STREAM: last_id},
                    count=10,
                    block=min(remaining_ms, 1000),
                )

                if not messages:
                    continue

                # Check if any message matches our activation_id
                for stream, stream_messages in messages:
                    for msg_id, msg_data in stream_messages:
                        last_id = msg_id
                        if msg_data.get("activation_id") == activation_id:
                            # Parse the message
                            result = self._parse_activation_message(msg_data)
                            logger.info(
                                f"Found activation result for {activation_id}"
                            )
                            return result

        except RedisError as e:
            logger.error(f"Error subscribing to activation: {e}")
            raise

    def get_activation_result(
        self, activation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Non-blocking check for activation result.

        Args:
            activation_id: The activation ID to look for

        Returns:
            dict: Activation record if found, None otherwise
        """
        try:
            # Read recent messages from activations stream
            messages = self.redis_client.xrevrange(
                self.ACTIVATIONS_STREAM, count=100
            )

            for msg_id, msg_data in messages:
                if msg_data.get("activation_id") == activation_id:
                    result = self._parse_activation_message(msg_data)
                    logger.info(
                        f"Found activation result for {activation_id} (non-blocking)"
                    )
                    return result

            logger.debug(
                f"No activation result found for {activation_id}"
            )
            return None

        except RedisError as e:
            logger.error(f"Error getting activation result: {e}")
            raise

    def _parse_activation_message(
        self, msg_data: Dict[str, str]
    ) -> Dict[str, Any]:
        """Parse activation message from Redis stream data.

        Args:
            msg_data: Raw message data from Redis

        Returns:
            dict: Parsed activation message
        """
        return {
            "activation_id": msg_data.get("activation_id"),
            "status_code": int(msg_data.get("status_code", 0)),
            "response": json.loads(msg_data.get("response", "{}")),
            "logs": json.loads(msg_data.get("logs", "[]")),
            "duration": int(msg_data.get("duration", 0)),
            "invoker_id": msg_data.get("invoker_id"),
        }

    def create_consumer_group(
        self, stream_name: str, group_name: str
    ) -> bool:
        """Create a consumer group for a stream.

        Args:
            stream_name: Name of the stream
            group_name: Name of the consumer group

        Returns:
            bool: True if created successfully, False if already exists
        """
        try:
            self.redis_client.xgroup_create(
                stream_name, group_name, id="0", mkstream=True
            )
            logger.info(
                f"Created consumer group '{group_name}' for stream '{stream_name}'"
            )
            return True

        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(
                    f"Consumer group '{group_name}' already exists for '{stream_name}'"
                )
                return False
            logger.error(f"Error creating consumer group: {e}")
            raise

        except RedisError as e:
            logger.error(f"Redis error creating consumer group: {e}")
            raise

    def get_invoker_health(self) -> List[Dict[str, Any]]:
        """Get recent invoker health heartbeats.

        Returns:
            list: List of heartbeat records with invoker status
        """
        try:
            # Read recent heartbeats (last 60 seconds worth)
            messages = self.redis_client.xrevrange(
                self.HEARTBEATS_STREAM, count=100
            )

            heartbeats = []
            seen_invokers = set()

            for msg_id, msg_data in messages:
                invoker_id = msg_data.get("invoker_id")

                # Only include the most recent heartbeat per invoker
                if invoker_id and invoker_id not in seen_invokers:
                    seen_invokers.add(invoker_id)
                    heartbeats.append(
                        {
                            "invoker_id": invoker_id,
                            "timestamp": int(msg_data.get("timestamp", 0)),
                            "capacity": int(msg_data.get("capacity", 0)),
                            "active_containers": int(
                                msg_data.get("active_containers", 0)
                            ),
                            "status": msg_data.get("status", "unknown"),
                        }
                    )

            logger.debug(
                f"Retrieved health for {len(heartbeats)} invokers"
            )
            return heartbeats

        except RedisError as e:
            logger.error(f"Error getting invoker health: {e}")
            raise

    def publish_activation_result(
        self, activation: ActivationMessage
    ) -> str:
        """Publish an activation result to the activations stream.

        Args:
            activation: ActivationMessage with result details

        Returns:
            str: Message ID from Redis XADD

        Raises:
            RedisError: If publishing fails
        """
        try:
            message_data = asdict(activation)
            # Convert complex types to JSON strings
            message_data["response"] = json.dumps(message_data["response"])
            message_data["logs"] = json.dumps(message_data["logs"])

            message_id = self.redis_client.xadd(
                self.ACTIVATIONS_STREAM,
                message_data,
                maxlen=self.max_stream_len,
                approximate=True,
            )

            logger.info(
                f"Published activation result {activation.activation_id} "
                f"with message ID {message_id}"
            )
            return message_id

        except RedisError as e:
            logger.error(f"Failed to publish activation result: {e}")
            raise

    def publish_heartbeat(self, heartbeat: HeartbeatMessage) -> str:
        """Publish an invoker heartbeat to the heartbeats stream.

        Args:
            heartbeat: HeartbeatMessage with invoker health data

        Returns:
            str: Message ID from Redis XADD

        Raises:
            RedisError: If publishing fails
        """
        try:
            message_data = asdict(heartbeat)

            message_id = self.redis_client.xadd(
                self.HEARTBEATS_STREAM,
                message_data,
                maxlen=self.max_stream_len,
                approximate=True,
            )

            logger.debug(
                f"Published heartbeat for invoker {heartbeat.invoker_id}"
            )
            return message_id

        except RedisError as e:
            logger.error(f"Failed to publish heartbeat: {e}")
            raise

    def close(self) -> None:
        """Close the Redis connection."""
        try:
            self.redis_client.close()
            logger.info("Closed Redis connection")
        except Exception as e:
            logger.warning(f"Error closing Redis connection: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
