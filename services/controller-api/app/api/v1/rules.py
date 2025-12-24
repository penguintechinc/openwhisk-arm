"""
OpenWhisk-compatible Rules API endpoints.

This module provides REST endpoints for managing OpenWhisk rules that bind
triggers to actions. Rules implement event-driven execution: when a trigger
fires, all active rules associated with that trigger invoke their actions.

Endpoints:
- GET /api/v1/namespaces/{namespace}/rules - List rules
- GET /api/v1/namespaces/{namespace}/rules/{ruleName} - Get rule details
- PUT /api/v1/namespaces/{namespace}/rules/{ruleName} - Create/update rule
- DELETE /api/v1/namespaces/{namespace}/rules/{ruleName} - Delete rule
- POST /api/v1/namespaces/{namespace}/rules/{ruleName} - Enable/disable rule
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    from pydal import DAL

# Create Blueprint
rules_bp = Blueprint('rules', __name__, url_prefix='/api/v1/namespaces/<namespace>/rules')


def _get_db() -> DAL:
    """Get PyDAL database connection.

    Returns:
        DAL: Database connection instance
    """
    from app.models import get_db
    return get_db()


def _get_namespace_id(db: DAL, namespace: str) -> int | None:
    """Get namespace ID by name.

    Args:
        db: PyDAL database connection
        namespace: Namespace name

    Returns:
        Namespace ID if found, None otherwise
    """
    result = db(db.namespace.name == namespace).select(db.namespace.id).first()
    return result.id if result else None


def _get_trigger(db: DAL, namespace_id: int, trigger_name: str) -> dict[str, Any] | None:
    """Get trigger by name in namespace.

    Args:
        db: PyDAL database connection
        namespace_id: Namespace ID
        trigger_name: Trigger name

    Returns:
        Trigger record dict if found, None otherwise
    """
    row = db(
        (db.trigger.namespace_id == namespace_id) &
        (db.trigger.name == trigger_name)
    ).select().first()

    if not row:
        return None

    return {
        'id': row.id,
        'name': row.name,
        'namespace': namespace_id,
        'version': row.version,
        'publish': row.publish,
        'parameters': row.parameters or {},
        'annotations': row.annotations or {},
    }


def _get_action(db: DAL, namespace_id: int, action_name: str) -> dict[str, Any] | None:
    """Get action by name in namespace.

    Args:
        db: PyDAL database connection
        namespace_id: Namespace ID
        action_name: Action name

    Returns:
        Action record dict if found, None otherwise
    """
    row = db(
        (db.action.namespace_id == namespace_id) &
        (db.action.name == action_name)
    ).select().first()

    if not row:
        return None

    return {
        'id': row.id,
        'name': row.name,
        'namespace': namespace_id,
        'version': row.version,
        'publish': row.publish,
        'annotations': row.annotations or {},
        'parameters': row.parameters or {},
    }


def _format_rule_response(
    db: DAL, namespace: str, rule_row: Any
) -> dict[str, Any]:
    """Format rule record for API response.

    Args:
        db: PyDAL database connection
        namespace: Namespace name
        rule_row: Rule database row

    Returns:
        Formatted rule dict with fully qualified names
    """
    # Get related trigger and action
    trigger_row = db(db.trigger.id == rule_row.trigger_id).select().first()
    action_row = db(db.action.id == rule_row.action_id).select().first()

    trigger_name = trigger_row.name if trigger_row else None
    action_name = action_row.name if action_row else None

    return {
        'namespace': namespace,
        'name': rule_row.name,
        'trigger': f'/{namespace}/{trigger_name}' if trigger_name else None,
        'action': f'/{namespace}/{action_name}' if action_name else None,
        'status': rule_row.status,
        'version': rule_row.version,
        'publish': rule_row.publish,
        'created': int(rule_row.created_at.timestamp() * 1000),
        'updated': int(rule_row.updated_at.timestamp() * 1000),
    }


@rules_bp.route('', methods=['GET'])
def list_rules(namespace: str) -> tuple[dict, int]:
    """List all rules in namespace.

    Args:
        namespace: Namespace name

    Returns:
        JSON response with list of rules and HTTP status code
    """
    db = _get_db()

    # Get namespace ID
    namespace_id = _get_namespace_id(db, namespace)
    if not namespace_id:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'NAMESPACE_NOT_FOUND',
                'message': f'Namespace {namespace} not found',
            }),
            404,
        )

    # Get all rules in namespace
    rules = db(db.rule.namespace_id == namespace_id).select(orderby=db.rule.name)

    # Format response
    rules_list = [_format_rule_response(db, namespace, rule) for rule in rules]

    return jsonify(rules_list), 200


@rules_bp.route('/<rule_name>', methods=['GET'])
def get_rule(namespace: str, rule_name: str) -> tuple[dict, int]:
    """Get rule details including trigger and action references.

    Args:
        namespace: Namespace name
        rule_name: Rule name

    Returns:
        JSON response with rule details and HTTP status code
    """
    db = _get_db()

    # Get namespace ID
    namespace_id = _get_namespace_id(db, namespace)
    if not namespace_id:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'NAMESPACE_NOT_FOUND',
                'message': f'Namespace {namespace} not found',
            }),
            404,
        )

    # Get rule
    rule = db(
        (db.rule.namespace_id == namespace_id) &
        (db.rule.name == rule_name)
    ).select().first()

    if not rule:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'RULE_NOT_FOUND',
                'message': f'Rule {rule_name} not found in namespace {namespace}',
            }),
            404,
        )

    # Format and return response
    response = _format_rule_response(db, namespace, rule)
    return jsonify(response), 200


@rules_bp.route('/<rule_name>', methods=['PUT'])
def create_or_update_rule(namespace: str, rule_name: str) -> tuple[dict, int]:
    """Create or update rule with overwrite parameter.

    Args:
        namespace: Namespace name
        rule_name: Rule name

    Returns:
        JSON response with created/updated rule and HTTP status code
    """
    db = _get_db()

    # Get query parameters
    overwrite = request.args.get('overwrite', 'false').lower() == 'true'

    # Get request body
    data = request.get_json()
    if not data:
        return (
            jsonify({
                'error': 'Bad Request',
                'code': 'INVALID_REQUEST',
                'message': 'Request body must be JSON',
            }),
            400,
        )

    # Validate required fields
    if 'trigger' not in data or 'action' not in data:
        return (
            jsonify({
                'error': 'Bad Request',
                'code': 'MISSING_REQUIRED_FIELD',
                'message': 'Required fields: trigger, action',
            }),
            400,
        )

    # Get namespace ID
    namespace_id = _get_namespace_id(db, namespace)
    if not namespace_id:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'NAMESPACE_NOT_FOUND',
                'message': f'Namespace {namespace} not found',
            }),
            404,
        )

    # Parse trigger name (format: /namespace/triggerName)
    trigger_ref = data['trigger']
    if trigger_ref.startswith('/'):
        trigger_parts = trigger_ref.strip('/').split('/')
        trigger_name = trigger_parts[-1]
    else:
        trigger_name = trigger_ref

    # Parse action name (format: /namespace/actionName)
    action_ref = data['action']
    if action_ref.startswith('/'):
        action_parts = action_ref.strip('/').split('/')
        action_name = action_parts[-1]
    else:
        action_name = action_ref

    # Validate trigger exists
    trigger = _get_trigger(db, namespace_id, trigger_name)
    if not trigger:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'TRIGGER_NOT_FOUND',
                'message': f'Trigger {trigger_name} not found in namespace {namespace}',
            }),
            404,
        )

    # Validate action exists
    action = _get_action(db, namespace_id, action_name)
    if not action:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'ACTION_NOT_FOUND',
                'message': f'Action {action_name} not found in namespace {namespace}',
            }),
            404,
        )

    # Check if rule already exists
    existing_rule = db(
        (db.rule.namespace_id == namespace_id) &
        (db.rule.name == rule_name)
    ).select().first()

    if existing_rule and not overwrite:
        return (
            jsonify({
                'error': 'Conflict',
                'code': 'RULE_ALREADY_EXISTS',
                'message': f'Rule {rule_name} already exists. Use ?overwrite=true to replace',
            }),
            409,
        )

    # Get status from request body (default: active)
    status = data.get('status', 'active')
    if status not in ['active', 'inactive']:
        return (
            jsonify({
                'error': 'Bad Request',
                'code': 'INVALID_STATUS',
                'message': 'Status must be "active" or "inactive"',
            }),
            400,
        )

    # Create or update rule
    if existing_rule:
        # Update existing rule
        existing_rule.update_record(
            trigger_id=trigger['id'],
            action_id=action['id'],
            status=status,
        )
        db.commit()
        rule_id = existing_rule.id
        status_code = 200
    else:
        # Create new rule
        rule_id = db.rule.insert(
            namespace_id=namespace_id,
            name=rule_name,
            trigger_id=trigger['id'],
            action_id=action['id'],
            status=status,
            version='0.0.1',
            publish=False,
        )
        db.commit()
        status_code = 201

    # Fetch created/updated rule
    rule = db(db.rule.id == rule_id).select().first()
    response = _format_rule_response(db, namespace, rule)

    return jsonify(response), status_code


@rules_bp.route('/<rule_name>', methods=['DELETE'])
def delete_rule(namespace: str, rule_name: str) -> tuple[dict, int]:
    """Delete rule.

    Args:
        namespace: Namespace name
        rule_name: Rule name

    Returns:
        JSON response with deletion confirmation and HTTP status code
    """
    db = _get_db()

    # Get namespace ID
    namespace_id = _get_namespace_id(db, namespace)
    if not namespace_id:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'NAMESPACE_NOT_FOUND',
                'message': f'Namespace {namespace} not found',
            }),
            404,
        )

    # Get rule
    rule = db(
        (db.rule.namespace_id == namespace_id) &
        (db.rule.name == rule_name)
    ).select().first()

    if not rule:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'RULE_NOT_FOUND',
                'message': f'Rule {rule_name} not found in namespace {namespace}',
            }),
            404,
        )

    # Format response before deletion
    response = _format_rule_response(db, namespace, rule)

    # Delete rule
    db(db.rule.id == rule.id).delete()
    db.commit()

    return jsonify(response), 200


@rules_bp.route('/<rule_name>', methods=['POST'])
def update_rule_status(namespace: str, rule_name: str) -> tuple[dict, int]:
    """Enable or disable rule by updating status.

    Args:
        namespace: Namespace name
        rule_name: Rule name

    Returns:
        JSON response with updated rule and HTTP status code
    """
    db = _get_db()

    # Get request body
    data = request.get_json()
    if not data or 'status' not in data:
        return (
            jsonify({
                'error': 'Bad Request',
                'code': 'MISSING_REQUIRED_FIELD',
                'message': 'Required field: status',
            }),
            400,
        )

    # Validate status value
    status = data['status']
    if status not in ['active', 'inactive']:
        return (
            jsonify({
                'error': 'Bad Request',
                'code': 'INVALID_STATUS',
                'message': 'Status must be "active" or "inactive"',
            }),
            400,
        )

    # Get namespace ID
    namespace_id = _get_namespace_id(db, namespace)
    if not namespace_id:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'NAMESPACE_NOT_FOUND',
                'message': f'Namespace {namespace} not found',
            }),
            404,
        )

    # Get rule
    rule = db(
        (db.rule.namespace_id == namespace_id) &
        (db.rule.name == rule_name)
    ).select().first()

    if not rule:
        return (
            jsonify({
                'error': 'Not Found',
                'code': 'RULE_NOT_FOUND',
                'message': f'Rule {rule_name} not found in namespace {namespace}',
            }),
            404,
        )

    # Update status
    rule.update_record(status=status)
    db.commit()

    # Format and return response
    response = _format_rule_response(db, namespace, rule)
    return jsonify(response), 200
