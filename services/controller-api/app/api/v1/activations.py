"""
OpenWhisk-compatible Activations API endpoints.

Provides REST API for querying and retrieving activation records.
Activations are execution records created when actions are invoked.

Endpoints:
    GET /api/v1/namespaces/{namespace}/activations
        List activations with optional filtering and pagination
    GET /api/v1/namespaces/{namespace}/activations/{activationId}
        Get full activation record
    GET /api/v1/namespaces/{namespace}/activations/{activationId}/logs
        Get activation logs array
    GET /api/v1/namespaces/{namespace}/activations/{activationId}/result
        Get activation result only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_security import auth_required, current_user

if TYPE_CHECKING:
    from pydal import DAL

# Create blueprint for activations endpoints
activations_bp = Blueprint(
    'activations',
    __name__,
    url_prefix='/api/v1/namespaces'
)


@activations_bp.route('/<string:namespace>/activations', methods=['GET'])
@auth_required()
def list_activations(namespace: str) -> Tuple[Dict[str, Any], int]:
    """
    List activations in a namespace with optional filtering and pagination.

    Query Parameters:
        limit (int): Maximum number of activations to return (default 30, max 200)
        skip (int): Number of activations to skip for pagination (default 0)
        name (str): Filter by action name (exact or partial match)
        since (int): Filter activations after timestamp (epoch milliseconds)
        upto (int): Filter activations before timestamp (epoch milliseconds)
        docs (bool): Include full activation documents (default false for efficiency)

    Returns:
        JSON array of activation records sorted by start time (descending)
        Each record includes: activationId, namespace, name, version,
                            start, end, duration, statusCode, response, logs, annotations

    Status Codes:
        200: Success
        400: Invalid query parameters
        401: Unauthorized
        404: Namespace not found
        500: Internal server error

    Examples:
        GET /api/v1/namespaces/user@example.com/activations?limit=10&since=1234567890000
        GET /api/v1/namespaces/user@example.com/activations?name=myaction&docs=true
        GET /api/v1/namespaces/user@example.com/activations?skip=10&limit=20
    """
    from app.models import get_db

    db: DAL = get_db()

    # Validate and extract query parameters
    try:
        limit = min(int(request.args.get('limit', 30)), 200)
        skip = max(0, int(request.args.get('skip', 0)))
        docs = request.args.get('docs', 'false').lower() == 'true'

        # Optional filters
        name_filter = request.args.get('name', None)
        since = int(request.args.get('since', 0)) if request.args.get('since') else 0
        upto = int(request.args.get('upto', float('inf'))) if request.args.get('upto') else float('inf')
    except ValueError as e:
        return jsonify({'error': f'Invalid query parameter: {str(e)}'}), 400

    if limit < 1 or limit > 200:
        return jsonify({'error': 'limit must be between 1 and 200'}), 400

    if skip < 0:
        return jsonify({'error': 'skip must be non-negative'}), 400

    try:
        # Verify namespace exists and user has access
        namespace_record = db(db.namespace.name == namespace).select().first()
        if not namespace_record:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Check authorization - user must own the namespace
        if namespace_record.owner_id != current_user.id:
            return jsonify({'error': f'Unauthorized access to namespace {namespace}'}), 401

        # Build query for activations
        query = db.activation.namespace_id == namespace_record.id

        # Apply time filters
        if since > 0:
            query = query & (db.activation.start >= since)
        if upto != float('inf'):
            query = query & (db.activation.start <= upto)

        # Apply action name filter
        if name_filter:
            # Support both exact and partial matches
            query = query & (db.activation.action_name.contains(name_filter))

        # Get total count before pagination
        total_count = db(query).count()

        # Execute query with pagination, sorted by start time descending
        rows = db(query).select(
            orderby=~db.activation.start,
            limitby=(skip, skip + limit)
        )

        # Format response based on docs parameter
        activations = []
        for row in rows:
            activation = _format_activation_record(row, include_full=docs)
            activations.append(activation)

        return jsonify({
            'activations': activations,
            'total': total_count,
            'skip': skip,
            'limit': limit
        }), 200

    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@activations_bp.route('/<string:namespace>/activations/<string:activation_id>', methods=['GET'])
@auth_required()
def get_activation(namespace: str, activation_id: str) -> Tuple[Dict[str, Any], int]:
    """
    Get full activation record by ID.

    Path Parameters:
        namespace (str): Namespace name
        activation_id (str): Activation UUID/ID

    Returns:
        Full activation record with all fields:
        {
            activationId, namespace, name, version,
            subject, start, end, duration,
            statusCode, response: {success, result},
            logs: [...], annotations: {...},
            cause (if part of sequence)
        }

    Status Codes:
        200: Success
        401: Unauthorized
        404: Namespace or activation not found
        500: Internal server error
    """
    from app.models import get_db

    db: DAL = get_db()

    try:
        # Verify namespace exists and user has access
        namespace_record = db(db.namespace.name == namespace).select().first()
        if not namespace_record:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Check authorization
        if namespace_record.owner_id != current_user.id:
            return jsonify({'error': f'Unauthorized access to namespace {namespace}'}), 401

        # Get activation record
        activation_row = db(
            (db.activation.activation_id == activation_id) &
            (db.activation.namespace_id == namespace_record.id)
        ).select().first()

        if not activation_row:
            return jsonify({
                'error': f'Activation {activation_id} not found in namespace {namespace}'
            }), 404

        # Format and return full activation record
        activation = _format_activation_record(activation_row, include_full=True)
        return jsonify(activation), 200

    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@activations_bp.route('/<string:namespace>/activations/<string:activation_id>/logs', methods=['GET'])
@auth_required()
def get_activation_logs(namespace: str, activation_id: str) -> Tuple[Any, int]:
    """
    Get activation logs as array.

    Returns only the logs array from activation record.
    Logs contain timestamped stdout/stderr messages from action execution.

    Path Parameters:
        namespace (str): Namespace name
        activation_id (str): Activation UUID/ID

    Returns:
        JSON array of log strings, each typically formatted as:
        "timestamp: stdout/stderr message"

    Status Codes:
        200: Success
        401: Unauthorized
        404: Namespace or activation not found
        500: Internal server error

    Example:
        [
            "2025-12-24T10:30:45.123Z stdout: Hello World",
            "2025-12-24T10:30:45.456Z stderr: Warning message"
        ]
    """
    from app.models import get_db

    db: DAL = get_db()

    try:
        # Verify namespace exists and user has access
        namespace_record = db(db.namespace.name == namespace).select().first()
        if not namespace_record:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Check authorization
        if namespace_record.owner_id != current_user.id:
            return jsonify({'error': f'Unauthorized access to namespace {namespace}'}), 401

        # Get activation record
        activation_row = db(
            (db.activation.activation_id == activation_id) &
            (db.activation.namespace_id == namespace_record.id)
        ).select(db.activation.logs).first()

        if not activation_row:
            return jsonify({
                'error': f'Activation {activation_id} not found in namespace {namespace}'
            }), 404

        # Return logs array (or empty array if null)
        logs = activation_row.logs or []
        return jsonify(logs), 200

    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@activations_bp.route('/<string:namespace>/activations/<string:activation_id>/result', methods=['GET'])
@auth_required()
def get_activation_result(namespace: str, activation_id: str) -> Tuple[Any, int]:
    """
    Get activation result only (success status and return value).

    Returns the response.result field from activation record.
    Useful for quick access to action return value without full record.

    Path Parameters:
        namespace (str): Namespace name
        activation_id (str): Activation UUID/ID

    Returns:
        JSON object: {success: boolean, result: <action-return-value>}

    Status Codes:
        200: Success
        401: Unauthorized
        404: Namespace or activation not found
        500: Internal server error

    Example:
        {
            "success": true,
            "result": {"message": "Processing complete", "count": 42}
        }
    """
    from app.models import get_db

    db: DAL = get_db()

    try:
        # Verify namespace exists and user has access
        namespace_record = db(db.namespace.name == namespace).select().first()
        if not namespace_record:
            return jsonify({'error': f'Namespace {namespace} not found'}), 404

        # Check authorization
        if namespace_record.owner_id != current_user.id:
            return jsonify({'error': f'Unauthorized access to namespace {namespace}'}), 401

        # Get activation record response field
        activation_row = db(
            (db.activation.activation_id == activation_id) &
            (db.activation.namespace_id == namespace_record.id)
        ).select(
            db.activation.response_success,
            db.activation.response_result
        ).first()

        if not activation_row:
            return jsonify({
                'error': f'Activation {activation_id} not found in namespace {namespace}'
            }), 404

        # Return response object
        response = {
            'success': activation_row.response_success,
            'result': activation_row.response_result or None
        }
        return jsonify(response), 200

    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


def _format_activation_record(
    row: Any,
    include_full: bool = False
) -> Dict[str, Any]:
    """
    Format database activation record to OpenWhisk API response format.

    Args:
        row: PyDAL database row
        include_full: Whether to include all fields or subset for list view

    Returns:
        Formatted activation record dictionary
    """
    record: Dict[str, Any] = {
        'activationId': row.activation_id,
        'namespace': row.namespace_id,
        'name': row.action_name,
        'version': row.action_version or '0.0.1',
        'start': row.start,
        'end': row.end,
        'duration': row.duration,
        'statusCode': row.status_code or 0,
        'response': {
            'success': row.response_success if hasattr(row, 'response_success') else True,
            'result': row.response_result if hasattr(row, 'response_result') else None
        }
    }

    # Include full details if requested
    if include_full:
        record['subject'] = row.subject if hasattr(row, 'subject') else None
        record['logs'] = row.logs or []
        record['annotations'] = row.annotations or {}

        # Include cause if it exists (for sequences/compositions)
        if row.cause:
            record['cause'] = row.cause

        # Include publish flag
        record['publish'] = row.publish or False

    return record
