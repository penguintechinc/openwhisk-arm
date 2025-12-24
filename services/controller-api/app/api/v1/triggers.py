"""
OpenWhisk Triggers API endpoints.

Implements REST API for managing event sources (triggers) in OpenWhisk.

Triggers represent named event channels that can be fired by external
event sources (feeds) or manually. When a trigger fires, all associated
rules evaluate and may invoke their actions.

Supported operations:
- GET /api/v1/namespaces/{namespace}/triggers - List triggers
- GET /api/v1/namespaces/{namespace}/triggers/{triggerName} - Get trigger
- PUT /api/v1/namespaces/{namespace}/triggers/{triggerName} - Create/update trigger
- DELETE /api/v1/namespaces/{namespace}/triggers/{triggerName} - Delete trigger
- POST /api/v1/namespaces/{namespace}/triggers/{triggerName} - Fire trigger

Feed triggers support external event sources like alarms, Kafka, message queues.
Trigger parameters are merged with fire parameters when evaluating rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import Blueprint, jsonify, request, current_app
from flask_security import auth_required

if TYPE_CHECKING:
    from flask import Response


def create_triggers_blueprint() -> Blueprint:
    """
    Create the triggers API blueprint.

    Returns:
        Blueprint: Flask blueprint with trigger endpoints.
    """
    bp = Blueprint('triggers', __name__, url_prefix='/api/v1')

    @bp.route('/namespaces/<namespace>/triggers', methods=['GET'])
    @auth_required()
    def list_triggers(namespace: str) -> tuple[dict, int]:
        """
        List all triggers in a namespace.

        Args:
            namespace: Namespace name

        Returns:
            JSON response with trigger list, status code
        """
        try:
            from app.models import get_db

            db = get_db()

            # Find namespace
            ns = db(db.namespace.name == namespace).select().first()
            if not ns:
                return {
                    'error': 'Namespace not found',
                    'code': 'NOT_FOUND'
                }, 404

            # List triggers in namespace
            triggers = db(db.trigger.namespace_id == ns.id).select()

            # Format response
            trigger_list = []
            for trigger in triggers:
                trigger_list.append(_format_trigger(trigger))

            return {
                'triggers': trigger_list
            }, 200

        except Exception as e:
            current_app.logger.error(f'Error listing triggers: {str(e)}')
            return {
                'error': 'Internal server error',
                'code': 'INTERNAL_ERROR',
                'details': str(e)
            }, 500

    @bp.route('/namespaces/<namespace>/triggers/<trigger_name>', methods=['GET'])
    @auth_required()
    def get_trigger(namespace: str, trigger_name: str) -> tuple[dict, int]:
        """
        Get trigger details.

        Args:
            namespace: Namespace name
            trigger_name: Trigger name

        Returns:
            JSON response with trigger details, status code
        """
        try:
            from app.models import get_db

            db = get_db()

            # Find namespace
            ns = db(db.namespace.name == namespace).select().first()
            if not ns:
                return {
                    'error': 'Namespace not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Find trigger
            trigger = db(
                (db.trigger.namespace_id == ns.id) &
                (db.trigger.name == trigger_name)
            ).select().first()

            if not trigger:
                return {
                    'error': 'Trigger not found',
                    'code': 'NOT_FOUND'
                }, 404

            return _format_trigger(trigger), 200

        except Exception as e:
            current_app.logger.error(f'Error getting trigger: {str(e)}')
            return {
                'error': 'Internal server error',
                'code': 'INTERNAL_ERROR',
                'details': str(e)
            }, 500

    @bp.route('/namespaces/<namespace>/triggers/<trigger_name>', methods=['PUT'])
    @auth_required()
    def create_or_update_trigger(namespace: str, trigger_name: str) -> tuple[dict, int]:
        """
        Create or update a trigger.

        Query parameters:
            overwrite: If true, update existing trigger; if false, error on exist

        Request body:
        {
            "parameters": {...},      # Optional: default parameters
            "annotations": {...},     # Optional: metadata
            "feed": "/whisk.system/alarms/alarm"  # Optional: feed action
        }

        Args:
            namespace: Namespace name
            trigger_name: Trigger name

        Returns:
            JSON response with created/updated trigger, status code
        """
        try:
            from app.models import get_db

            db = get_db()

            # Parse request body
            body = request.get_json(force=True) if request.data else {}
            parameters = body.get('parameters', {})
            annotations = body.get('annotations', {})
            feed = body.get('feed')
            overwrite = request.args.get('overwrite', 'false').lower() == 'true'

            # Validate inputs
            if not isinstance(parameters, dict):
                return {
                    'error': 'Invalid parameters',
                    'code': 'INVALID_ARGUMENT'
                }, 400
            if not isinstance(annotations, dict):
                return {
                    'error': 'Invalid annotations',
                    'code': 'INVALID_ARGUMENT'
                }, 400

            # Find namespace
            ns = db(db.namespace.name == namespace).select().first()
            if not ns:
                return {
                    'error': 'Namespace not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Check if trigger exists
            existing = db(
                (db.trigger.namespace_id == ns.id) &
                (db.trigger.name == trigger_name)
            ).select().first()

            if existing and not overwrite:
                return {
                    'error': 'Trigger already exists',
                    'code': 'CONFLICT'
                }, 409

            status_code = 200
            if existing:
                # Update existing trigger
                existing.update_record(
                    parameters=parameters,
                    annotations=annotations,
                    feed=feed,
                    updated_at=datetime.utcnow()
                )
                db.commit()
                trigger = existing
            else:
                # Create new trigger
                trigger_id = db.trigger.insert(
                    namespace_id=ns.id,
                    name=trigger_name,
                    version='0.0.1',
                    publish=False,
                    parameters=parameters,
                    annotations=annotations,
                    feed=feed,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.commit()
                trigger = db.trigger[trigger_id]
                status_code = 201

            return _format_trigger(trigger), status_code

        except Exception as e:
            current_app.logger.error(f'Error creating/updating trigger: {str(e)}')
            return {
                'error': 'Internal server error',
                'code': 'INTERNAL_ERROR',
                'details': str(e)
            }, 500

    @bp.route('/namespaces/<namespace>/triggers/<trigger_name>', methods=['DELETE'])
    @auth_required()
    def delete_trigger(namespace: str, trigger_name: str) -> tuple[dict, int]:
        """
        Delete a trigger.

        Args:
            namespace: Namespace name
            trigger_name: Trigger name

        Returns:
            JSON response with deleted trigger, status code
        """
        try:
            from app.models import get_db

            db = get_db()

            # Find namespace
            ns = db(db.namespace.name == namespace).select().first()
            if not ns:
                return {
                    'error': 'Namespace not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Find trigger
            trigger = db(
                (db.trigger.namespace_id == ns.id) &
                (db.trigger.name == trigger_name)
            ).select().first()

            if not trigger:
                return {
                    'error': 'Trigger not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Store trigger data before deletion
            trigger_data = _format_trigger(trigger)

            # Delete trigger (cascade deletes associated rules)
            db(db.trigger.id == trigger.id).delete()
            db.commit()

            return trigger_data, 200

        except Exception as e:
            current_app.logger.error(f'Error deleting trigger: {str(e)}')
            return {
                'error': 'Internal server error',
                'code': 'INTERNAL_ERROR',
                'details': str(e)
            }, 500

    @bp.route('/namespaces/<namespace>/triggers/<trigger_name>', methods=['POST'])
    @auth_required()
    def fire_trigger(namespace: str, trigger_name: str) -> tuple[dict, int]:
        """
        Fire a trigger (invoke all associated rules).

        When a trigger is fired:
        1. Find all active rules for this trigger
        2. Merge trigger parameters with fire parameters
        3. Create activation for each action
        4. Return activation IDs

        Request body:
        {
            "param1": "value1",      # Fire parameters merged with trigger params
            "param2": "value2"
        }

        Args:
            namespace: Namespace name
            trigger_name: Trigger name

        Returns:
            JSON response with activation IDs, status code
        """
        try:
            from app.models import get_db

            db = get_db()

            # Parse fire parameters
            fire_params = request.get_json(force=True) if request.data else {}
            if not isinstance(fire_params, dict):
                return {
                    'error': 'Invalid parameters',
                    'code': 'INVALID_ARGUMENT'
                }, 400

            # Find namespace
            ns = db(db.namespace.name == namespace).select().first()
            if not ns:
                return {
                    'error': 'Namespace not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Find trigger
            trigger = db(
                (db.trigger.namespace_id == ns.id) &
                (db.trigger.name == trigger_name)
            ).select().first()

            if not trigger:
                return {
                    'error': 'Trigger not found',
                    'code': 'NOT_FOUND'
                }, 404

            # Get invocation service
            from app.services import get_invocation

            invocation_service = get_invocation()
            if not invocation_service:
                return {
                    'error': 'Invocation service unavailable',
                    'code': 'SERVICE_UNAVAILABLE'
                }, 503

            # Get subject from authentication context
            # TODO: Get actual subject from Flask-Security context
            subject = namespace  # Placeholder

            # Invoke trigger using invocation service
            try:
                activation_ids = invocation_service.invoke_trigger(
                    namespace=namespace,
                    trigger_name=trigger_name,
                    params=fire_params,
                    subject=subject
                )

                return {
                    'activationIds': activation_ids,
                    'activationId': activation_ids[0] if activation_ids else None
                }, 202

            except ValueError as e:
                # Trigger not found
                return {
                    'error': str(e),
                    'code': 'NOT_FOUND'
                }, 404
            except Exception as e:
                current_app.logger.error(f'Error firing trigger: {e}')
                return {
                    'error': 'Internal server error',
                    'code': 'INTERNAL_ERROR',
                    'details': str(e)
                }, 500

        except Exception as e:
            current_app.logger.error(f'Error firing trigger: {str(e)}')
            return {
                'error': 'Internal server error',
                'code': 'INTERNAL_ERROR',
                'details': str(e)
            }, 500

    return bp


def _format_trigger(trigger: Any) -> dict:
    """
    Format a trigger record as JSON response.

    Args:
        trigger: PyDAL trigger record

    Returns:
        Dictionary with trigger data in OpenWhisk format
    """
    return {
        'namespace': trigger.namespace_id,  # Will be namespace name in real impl
        'name': trigger.name,
        'version': trigger.version,
        'publish': trigger.publish,
        'annotations': trigger.annotations or {},
        'parameters': trigger.parameters or {},
        'feed': trigger.feed,
        'updated': int(trigger.updated_at.timestamp() * 1000) if trigger.updated_at else 0,
        'created': int(trigger.created_at.timestamp() * 1000) if trigger.created_at else 0
    }
