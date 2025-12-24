"""
OpenWhisk Actions API v1 endpoint.

This module implements the OpenWhisk-compatible REST API for managing actions.
Actions are serverless functions that can be invoked with parameters.

API Endpoints:
    GET    /api/v1/namespaces/{namespace}/actions
    GET    /api/v1/namespaces/{namespace}/actions/{actionName}
    GET    /api/v1/namespaces/{namespace}/actions/{packageName}/{actionName}
    PUT    /api/v1/namespaces/{namespace}/actions/{actionName}?overwrite=true
    DELETE /api/v1/namespaces/{namespace}/actions/{actionName}
    POST   /api/v1/namespaces/{namespace}/actions/{actionName}?blocking=true&result=true

Action Format:
    {
        "namespace": "user@email.com",
        "name": "actionName",
        "version": "0.0.1",
        "publish": false,
        "exec": {
            "kind": "python:3.13",
            "code": "def main(args): return args",
            "binary": false,
            "main": "main",
            "image": "custom/image:tag",
            "components": ["action1", "action2"]  # For sequences
        },
        "limits": {
            "timeout": 60000,
            "memory": 256,
            "logs": 10,
            "concurrency": 1
        },
        "parameters": [
            {"key": "param1", "value": "value1"}
        ],
        "annotations": [
            {"key": "web-export", "value": true}
        ]
    }
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, current_app
from pydal import DAL

from app.models.database import get_database_manager

# Create blueprint
actions_bp = Blueprint('actions', __name__)


def get_db() -> DAL:
    """Get thread-local database connection."""
    db_manager = get_database_manager()
    return db_manager.get_thread_connection()


def normalize_params_annotations(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert OpenWhisk parameter/annotation format to dictionary.

    OpenWhisk format: [{"key": "k1", "value": "v1"}, ...]
    Internal format: {"k1": "v1", ...}
    """
    if not items:
        return {}
    return {item['key']: item['value'] for item in items}


def denormalize_params_annotations(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert internal dictionary format to OpenWhisk parameter/annotation format.

    Internal format: {"k1": "v1", ...}
    OpenWhisk format: [{"key": "k1", "value": "v1"}, ...]
    """
    if not data:
        return []
    return [{'key': k, 'value': v} for k, v in data.items()]


def compute_code_hash(code: str) -> str:
    """Compute SHA256 hash of action code."""
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def parse_action_path(namespace: str, action_path: str) -> tuple[str, Optional[str], str]:
    """
    Parse action path into namespace, package, and action name.

    Args:
        namespace: Namespace from URL path
        action_path: Action path (actionName or packageName/actionName)

    Returns:
        Tuple of (namespace, package_name or None, action_name)
    """
    parts = action_path.split('/')
    if len(parts) == 1:
        return namespace, None, parts[0]
    elif len(parts) == 2:
        return namespace, parts[0], parts[1]
    else:
        return jsonify({'error': 'Invalid action path'}), 400


def format_action_response(action: Any, include_code: bool = False) -> Dict[str, Any]:
    """
    Format action database record as OpenWhisk API response.

    Args:
        action: PyDAL Row object representing action
        include_code: Whether to include code in response (default: False)

    Returns:
        Action in OpenWhisk API format
    """
    db = get_db()

    # Get namespace name
    namespace = db.namespace[action.namespace_id]
    namespace_name = namespace.name if namespace else 'unknown'

    # Get package name if applicable
    package_name = None
    if action.package_id:
        package = db.package[action.package_id]
        package_name = package.name if package else None

    # Build fully qualified name
    if package_name:
        fqn = f"/{namespace_name}/{package_name}/{action.name}"
    else:
        fqn = f"/{namespace_name}/{action.name}"

    # Build exec section
    exec_data = {
        'kind': action.exec_kind,
        'binary': action.exec_binary,
        'main': action.exec_main,
    }

    if action.exec_image:
        exec_data['image'] = action.exec_image

    if action.exec_components:
        exec_data['components'] = action.exec_components

    # Only include code if explicitly requested
    if include_code:
        # TODO: Retrieve code from MinIO using exec_code_hash
        exec_data['code'] = f"# Code hash: {action.exec_code_hash}"

    # Build limits section
    limits_data = {
        'timeout': action.limits_timeout,
        'memory': action.limits_memory,
        'logs': action.limits_logs,
        'concurrency': action.limits_concurrency,
    }

    # Format response
    response = {
        'namespace': namespace_name,
        'name': action.name,
        'version': action.version,
        'publish': action.publish,
        'exec': exec_data,
        'limits': limits_data,
        'parameters': denormalize_params_annotations(action.parameters or {}),
        'annotations': denormalize_params_annotations(action.annotations or {}),
        'updated': int(action.updated_at.timestamp() * 1000) if action.updated_at else None,
    }

    return response


@actions_bp.route('/namespaces/<namespace>/actions', methods=['GET'])
def list_actions(namespace: str) -> tuple[Any, int]:
    """
    List all actions in a namespace.

    Query Parameters:
        limit: Maximum number of actions to return (default: 30)
        skip: Number of actions to skip (default: 0)

    Returns:
        JSON array of action metadata (without code)
    """
    db = get_db()

    # Get namespace record
    ns_record = db(db.namespace.name == namespace).select().first()
    if not ns_record:
        return jsonify({'error': f'Namespace {namespace} not found'}), 404

    # Parse query parameters
    limit = request.args.get('limit', 30, type=int)
    skip = request.args.get('skip', 0, type=int)

    # Query actions
    query = db.action.namespace_id == ns_record.id
    actions = db(query).select(
        orderby=db.action.name,
        limitby=(skip, skip + limit)
    )

    # Format response
    result = [format_action_response(action, include_code=False) for action in actions]

    return jsonify(result), 200


@actions_bp.route('/namespaces/<namespace>/actions/<path:action_path>', methods=['GET'])
def get_action(namespace: str, action_path: str) -> tuple[Any, int]:
    """
    Get action metadata (without code by default).

    Path Parameters:
        namespace: Namespace name
        action_path: Action name or package/action

    Query Parameters:
        code: Set to 'true' to include code in response

    Returns:
        JSON action metadata
    """
    db = get_db()

    # Parse action path
    parsed = parse_action_path(namespace, action_path)
    if len(parsed) == 2:  # Error response
        return parsed
    namespace_name, package_name, action_name = parsed

    # Get namespace record
    ns_record = db(db.namespace.name == namespace_name).select().first()
    if not ns_record:
        return jsonify({'error': f'Namespace {namespace_name} not found'}), 404

    # Build query
    query = (db.action.namespace_id == ns_record.id) & (db.action.name == action_name)

    if package_name:
        # Get package record
        pkg_record = db((db.package.namespace_id == ns_record.id) &
                       (db.package.name == package_name)).select().first()
        if not pkg_record:
            return jsonify({'error': f'Package {package_name} not found'}), 404
        query &= (db.action.package_id == pkg_record.id)
    else:
        query &= (db.action.package_id == None)

    # Get action
    action = db(query).select().first()
    if not action:
        return jsonify({'error': f'Action {action_path} not found'}), 404

    # Check if code should be included
    include_code = request.args.get('code', 'false').lower() == 'true'

    return jsonify(format_action_response(action, include_code=include_code)), 200


@actions_bp.route('/namespaces/<namespace>/actions/<path:action_path>', methods=['PUT'])
def create_or_update_action(namespace: str, action_path: str) -> tuple[Any, int]:
    """
    Create or update an action.

    Path Parameters:
        namespace: Namespace name
        action_path: Action name or package/action

    Query Parameters:
        overwrite: Set to 'true' to allow updating existing action

    Request Body:
        {
            "exec": {
                "kind": "python:3.13",
                "code": "def main(args): return args",
                "binary": false,
                "main": "main",
                "image": "custom/image:tag",
                "components": ["action1", "action2"]
            },
            "limits": {
                "timeout": 60000,
                "memory": 256,
                "logs": 10,
                "concurrency": 1
            },
            "parameters": [{"key": "k1", "value": "v1"}],
            "annotations": [{"key": "web-export", "value": true}]
        }

    Returns:
        JSON action metadata
    """
    db = get_db()

    # Parse action path
    parsed = parse_action_path(namespace, action_path)
    if len(parsed) == 2:  # Error response
        return parsed
    namespace_name, package_name, action_name = parsed

    # Get namespace record
    ns_record = db(db.namespace.name == namespace_name).select().first()
    if not ns_record:
        return jsonify({'error': f'Namespace {namespace_name} not found'}), 404

    # Get package record if specified
    package_id = None
    if package_name:
        pkg_record = db((db.package.namespace_id == ns_record.id) &
                       (db.package.name == package_name)).select().first()
        if not pkg_record:
            return jsonify({'error': f'Package {package_name} not found'}), 404
        package_id = pkg_record.id

    # Parse request body
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    # Validate exec section
    if 'exec' not in data:
        return jsonify({'error': 'exec section required'}), 400

    exec_data = data['exec']
    if 'kind' not in exec_data:
        return jsonify({'error': 'exec.kind required'}), 400

    # Check if action exists
    query = (db.action.namespace_id == ns_record.id) & (db.action.name == action_name)
    if package_id:
        query &= (db.action.package_id == package_id)
    else:
        query &= (db.action.package_id == None)

    existing_action = db(query).select().first()

    # Check overwrite flag
    overwrite = request.args.get('overwrite', 'false').lower() == 'true'
    if existing_action and not overwrite:
        return jsonify({'error': 'Action already exists. Use ?overwrite=true to update'}), 409

    # Compute code hash
    code = exec_data.get('code', '')
    code_hash = compute_code_hash(code)

    # TODO: Store actual code in MinIO using code_hash as key
    # For now, we just store the hash

    # Build action data
    action_data = {
        'namespace_id': ns_record.id,
        'package_id': package_id,
        'name': action_name,
        'version': data.get('version', '0.0.1'),
        'publish': data.get('publish', False),
        'exec_kind': exec_data['kind'],
        'exec_code_hash': code_hash,
        'exec_image': exec_data.get('image'),
        'exec_binary': exec_data.get('binary', False),
        'exec_main': exec_data.get('main', 'main'),
        'exec_components': exec_data.get('components'),
        'parameters': normalize_params_annotations(data.get('parameters', [])),
        'annotations': normalize_params_annotations(data.get('annotations', [])),
    }

    # Add limits if provided
    if 'limits' in data:
        limits = data['limits']
        action_data['limits_timeout'] = limits.get('timeout', 60000)
        action_data['limits_memory'] = limits.get('memory', 256)
        action_data['limits_logs'] = limits.get('logs', 10)
        action_data['limits_concurrency'] = limits.get('concurrency', 1)

    # Create or update action
    if existing_action:
        db(query).update(**action_data)
        action = db(query).select().first()
    else:
        action_id = db.action.insert(**action_data)
        action = db.action[action_id]

    db.commit()

    return jsonify(format_action_response(action, include_code=False)), 200


@actions_bp.route('/namespaces/<namespace>/actions/<path:action_path>', methods=['DELETE'])
def delete_action(namespace: str, action_path: str) -> tuple[Any, int]:
    """
    Delete an action.

    Path Parameters:
        namespace: Namespace name
        action_path: Action name or package/action

    Returns:
        Empty response with 204 status code
    """
    db = get_db()

    # Parse action path
    parsed = parse_action_path(namespace, action_path)
    if len(parsed) == 2:  # Error response
        return parsed
    namespace_name, package_name, action_name = parsed

    # Get namespace record
    ns_record = db(db.namespace.name == namespace_name).select().first()
    if not ns_record:
        return jsonify({'error': f'Namespace {namespace_name} not found'}), 404

    # Build query
    query = (db.action.namespace_id == ns_record.id) & (db.action.name == action_name)

    if package_name:
        # Get package record
        pkg_record = db((db.package.namespace_id == ns_record.id) &
                       (db.package.name == package_name)).select().first()
        if not pkg_record:
            return jsonify({'error': f'Package {package_name} not found'}), 404
        query &= (db.action.package_id == pkg_record.id)
    else:
        query &= (db.action.package_id == None)

    # Check if action exists
    action = db(query).select().first()
    if not action:
        return jsonify({'error': f'Action {action_path} not found'}), 404

    # Delete action
    db(query).delete()
    db.commit()

    # TODO: Delete code from MinIO using action.exec_code_hash

    return '', 204


@actions_bp.route('/namespaces/<namespace>/actions/<path:action_path>', methods=['POST'])
def invoke_action(namespace: str, action_path: str) -> tuple[Any, int]:
    """
    Invoke an action.

    Path Parameters:
        namespace: Namespace name
        action_path: Action name or package/action

    Query Parameters:
        blocking: Set to 'true' to wait for result (default: false)
        result: Set to 'true' to return only result, not full activation (default: false)

    Request Body:
        Action parameters as JSON object

    Returns:
        If blocking=false: Activation ID
        If blocking=true, result=false: Full activation record
        If blocking=true, result=true: Action result only
    """
    db = get_db()

    # Parse action path
    parsed = parse_action_path(namespace, action_path)
    if len(parsed) == 2:  # Error response
        return parsed
    namespace_name, package_name, action_name = parsed

    # Get namespace record
    ns_record = db(db.namespace.name == namespace_name).select().first()
    if not ns_record:
        return jsonify({'error': f'Namespace {namespace_name} not found'}), 404

    # Build query
    query = (db.action.namespace_id == ns_record.id) & (db.action.name == action_name)

    if package_name:
        # Get package record
        pkg_record = db((db.package.namespace_id == ns_record.id) &
                       (db.package.name == package_name)).select().first()
        if not pkg_record:
            return jsonify({'error': f'Package {package_name} not found'}), 404
        query &= (db.action.package_id == pkg_record.id)
    else:
        query &= (db.action.package_id == None)

    # Get action
    action = db(query).select().first()
    if not action:
        return jsonify({'error': f'Action {action_path} not found'}), 404

    # Parse query parameters
    blocking = request.args.get('blocking', 'false').lower() == 'true'
    result_only = request.args.get('result', 'false').lower() == 'true'

    # Parse request body (action parameters)
    params = request.get_json() or {}

    # Get invocation service
    from app.services import get_invocation

    invocation_service = get_invocation()
    if not invocation_service:
        return jsonify({'error': 'Invocation service unavailable'}), 503

    # Build action path
    if package_name:
        action_path = f"{package_name}/{action_name}"
    else:
        action_path = action_name

    # Get subject from authentication context
    # TODO: Get actual subject from Flask-Security context
    subject = namespace_name  # Placeholder

    try:
        # Invoke action using invocation service
        result = invocation_service.invoke_action(
            namespace=namespace_name,
            action_name=action_path,
            params=params,
            blocking=blocking,
            result_only=result_only,
            subject=subject
        )

        if not blocking:
            # Non-blocking: return activation ID immediately
            return jsonify(result), 202
        else:
            # Blocking: return result
            if result_only:
                # Return only result payload
                return jsonify(result), 200
            else:
                # Return full activation record
                return jsonify(result), 200

    except ValueError as e:
        # Action not found or invalid parameters
        return jsonify({'error': str(e)}), 404
    except TimeoutError as e:
        # Activation timed out
        return jsonify({'error': str(e)}), 504
    except Exception as e:
        # Internal error
        current_app.logger.error(f"Invocation error: {e}")
        return jsonify({'error': 'Internal server error'}), 500
