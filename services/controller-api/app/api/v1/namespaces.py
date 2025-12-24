"""OpenWhisk Namespaces API endpoint.

This module provides REST API endpoints for managing OpenWhisk namespaces.
Namespaces provide multi-tenancy isolation for actions, triggers, rules, and packages.

OpenWhisk compatible endpoints:
- GET /api/v1/namespaces - List all namespaces accessible to authenticated user
- GET /api/v1/namespaces/{namespace} - Get namespace details
- GET /api/v1/namespaces/{namespace}/limits - Get namespace limits/quotas
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Blueprint, current_app, jsonify, request
from flask_security import auth_required, current_user

from app.models import get_db

if TYPE_CHECKING:
    from flask import Response

# Create blueprint for namespaces API
namespaces_bp = Blueprint("namespaces", __name__, url_prefix="/namespaces")


@namespaces_bp.route("", methods=["GET"])
@auth_required()
def list_namespaces() -> tuple[dict[str, Any], int]:
    """List all namespaces accessible to authenticated user.

    OpenWhisk compatible endpoint to retrieve all namespaces the user has access to.
    Returns a list of namespace names including the default underscore namespace.

    Returns:
        Tuple of (JSON response, HTTP status code):
        - 200: List of namespaces
        - 401: Unauthorized (no authentication)
        - 500: Internal server error

    Response format:
        ["namespace1", "namespace2", "_"]
    """
    try:
        db = get_db()

        # Get all namespaces owned by current user
        namespaces = db(db.namespace.owner_id == current_user.id).select(
            db.namespace.name, orderby=db.namespace.name
        )

        namespace_names = [ns.name for ns in namespaces]

        # Always include default namespace
        if "_" not in namespace_names:
            namespace_names.append("_")

        return jsonify(namespace_names), 200

    except Exception as e:
        current_app.logger.error(f"Error listing namespaces: {str(e)}")
        return (
            jsonify(
                {
                    "error": "Internal server error",
                    "message": "Failed to retrieve namespaces",
                }
            ),
            500,
        )


@namespaces_bp.route("/<string:namespace>", methods=["GET"])
@auth_required()
def get_namespace(namespace: str) -> tuple[dict[str, Any], int]:
    """Get namespace details.

    OpenWhisk compatible endpoint to retrieve detailed information about a specific namespace.
    Includes namespace metadata and quota limits.

    Args:
        namespace: Namespace name or "_" for default namespace.

    Returns:
        Tuple of (JSON response, HTTP status code):
        - 200: Namespace details with limits
        - 401: Unauthorized (no authentication)
        - 403: Forbidden (user does not have access to namespace)
        - 404: Namespace not found
        - 500: Internal server error

    Response format:
        {
            "name": "namespace1",
            "uuid": "550e8400-e29b-41d4-a716-446655440000",
            "limits": {
                "concurrent_invocations": 100,
                "invocations_per_minute": 1000,
                "max_action_memory": 512,
                "max_action_timeout": 60000,
                "max_action_logs": 10,
                "max_parameters_size": 1048576
            }
        }
    """
    try:
        db = get_db()

        # Handle default namespace
        if namespace == "_":
            namespace = f"default_{current_user.id}"

        # Query namespace
        ns = db(
            (db.namespace.name == namespace) & (db.namespace.owner_id == current_user.id)
        ).select(db.namespace.ALL)

        if not ns:
            return (
                jsonify(
                    {
                        "error": "Namespace not found",
                        "message": f"Namespace '{namespace}' not found or not accessible",
                    }
                ),
                404,
            )

        namespace_record = ns[0]

        # Query limits for this namespace
        limits = db(db.namespace_limits.namespace_id == namespace_record.id).select()

        # Build response
        response_data = {
            "name": namespace_record.name,
            "uuid": namespace_record.uuid if hasattr(namespace_record, "uuid") else None,
            "limits": {},
        }

        if limits:
            limit_record = limits[0]
            response_data["limits"] = {
                "concurrent_invocations": limit_record.concurrent_invocations,
                "invocations_per_minute": limit_record.invocations_per_minute,
                "max_action_memory": limit_record.max_action_memory,
                "max_action_timeout": limit_record.max_action_timeout,
                "max_action_logs": limit_record.max_action_logs,
                "max_parameters_size": limit_record.max_parameters_size,
            }
        else:
            # Return default limits if none set
            response_data["limits"] = {
                "concurrent_invocations": 100,
                "invocations_per_minute": 1000,
                "max_action_memory": 512,
                "max_action_timeout": 60000,
                "max_action_logs": 10,
                "max_parameters_size": 1048576,
            }

        return jsonify(response_data), 200

    except Exception as e:
        current_app.logger.error(
            f"Error retrieving namespace '{namespace}': {str(e)}"
        )
        return (
            jsonify(
                {
                    "error": "Internal server error",
                    "message": "Failed to retrieve namespace details",
                }
            ),
            500,
        )


@namespaces_bp.route("/<string:namespace>/limits", methods=["GET"])
@auth_required()
def get_namespace_limits(namespace: str) -> tuple[dict[str, Any], int]:
    """Get namespace limits and quotas.

    OpenWhisk compatible endpoint to retrieve quota limits for a specific namespace.

    Args:
        namespace: Namespace name or "_" for default namespace.

    Returns:
        Tuple of (JSON response, HTTP status code):
        - 200: Namespace limits/quotas
        - 401: Unauthorized (no authentication)
        - 403: Forbidden (user does not have access to namespace)
        - 404: Namespace not found
        - 500: Internal server error

    Response format:
        {
            "concurrent_invocations": 100,
            "invocations_per_minute": 1000,
            "max_action_memory": 512,
            "max_action_timeout": 60000,
            "max_action_logs": 10,
            "max_parameters_size": 1048576
        }
    """
    try:
        db = get_db()

        # Handle default namespace
        if namespace == "_":
            namespace = f"default_{current_user.id}"

        # Query namespace
        ns = db(
            (db.namespace.name == namespace) & (db.namespace.owner_id == current_user.id)
        ).select()

        if not ns:
            return (
                jsonify(
                    {
                        "error": "Namespace not found",
                        "message": f"Namespace '{namespace}' not found or not accessible",
                    }
                ),
                404,
            )

        namespace_record = ns[0]

        # Query limits for this namespace
        limits = db(db.namespace_limits.namespace_id == namespace_record.id).select()

        # Build response with limits
        if limits:
            limit_record = limits[0]
            limits_data = {
                "concurrent_invocations": limit_record.concurrent_invocations,
                "invocations_per_minute": limit_record.invocations_per_minute,
                "max_action_memory": limit_record.max_action_memory,
                "max_action_timeout": limit_record.max_action_timeout,
                "max_action_logs": limit_record.max_action_logs,
                "max_parameters_size": limit_record.max_parameters_size,
            }
        else:
            # Return default limits if none set
            limits_data = {
                "concurrent_invocations": 100,
                "invocations_per_minute": 1000,
                "max_action_memory": 512,
                "max_action_timeout": 60000,
                "max_action_logs": 10,
                "max_parameters_size": 1048576,
            }

        return jsonify(limits_data), 200

    except Exception as e:
        current_app.logger.error(
            f"Error retrieving limits for namespace '{namespace}': {str(e)}"
        )
        return (
            jsonify(
                {
                    "error": "Internal server error",
                    "message": "Failed to retrieve namespace limits",
                }
            ),
            500,
        )
