"""OpenWhisk Web Actions API - public HTTP endpoints without authentication."""

from __future__ import annotations

import base64
import json
from typing import Any

from flask import Blueprint, Response, make_response, request
from pydal import DAL

from app.extensions import get_db

web_bp = Blueprint("web", __name__)


def _get_annotation_value(annotations: list[dict[str, Any]], key: str, default: Any = None) -> Any:
    """Extract annotation value from annotations list.

    Args:
        annotations: List of annotation dicts with 'key' and 'value' fields
        key: Annotation key to search for
        default: Default value if not found

    Returns:
        Annotation value or default
    """
    for annotation in annotations or []:
        if annotation.get("key") == key:
            return annotation.get("value", default)
    return default


def _parse_web_path(path: str) -> tuple[str, str, str, str]:
    """Parse web action path into components.

    Path format: /web/{namespace}/{package}/{action}.{extension}
    Or: /web/{namespace}/default/{action}.{extension}

    Args:
        path: Request path after /api/v1/web/

    Returns:
        Tuple of (namespace, package, action, extension)

    Raises:
        ValueError: If path format is invalid
    """
    parts = path.strip("/").split("/")

    if len(parts) < 3:
        raise ValueError("Invalid web action path format")

    namespace = parts[0]
    package = parts[1]

    # Last part contains action.extension
    action_ext = parts[2]
    if "." not in action_ext:
        raise ValueError("Missing extension in web action path")

    action, extension = action_ext.rsplit(".", 1)

    return namespace, package, action, extension


def _build_ow_parameters(action_name: str, extension: str) -> dict[str, Any]:
    """Build __ow_* parameters from HTTP request.

    Args:
        action_name: Action name being invoked
        extension: Response extension (.json, .html, etc)

    Returns:
        Dictionary of __ow_* parameters
    """
    params = {
        "__ow_method": request.method.lower(),
        "__ow_headers": dict(request.headers),
        "__ow_path": request.path,
        "__ow_query": request.args.to_dict(flat=False),
    }

    # Add request body
    if request.data:
        content_type = request.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                params["__ow_body"] = request.get_json()
            except Exception:
                params["__ow_body"] = base64.b64encode(request.data).decode("utf-8")
        else:
            params["__ow_body"] = base64.b64encode(request.data).decode("utf-8")

    # Add authenticated user if present (from JWT or other auth)
    # Web actions don't require auth, but if present, pass it through
    if hasattr(request, "user") and request.user:
        params["__ow_user"] = request.user

    return params


def _transform_response(result: dict[str, Any], extension: str) -> Response:
    """Transform action result based on extension.

    Args:
        result: Action invocation result
        extension: Response extension (.json, .html, .http, .text, .svg)

    Returns:
        Flask Response object
    """
    if extension == "json":
        # Return result as JSON
        return make_response(result, 200, {"Content-Type": "application/json"})

    elif extension == "html":
        # Return result.body as HTML
        body = result.get("body", "")
        if isinstance(body, dict):
            body = json.dumps(body)
        return make_response(body, 200, {"Content-Type": "text/html"})

    elif extension == "http":
        # Full HTTP response control
        status_code = result.get("statusCode", 200)
        headers = result.get("headers", {})
        body = result.get("body", "")

        if isinstance(body, dict):
            body = json.dumps(body)
            headers.setdefault("Content-Type", "application/json")

        return make_response(body, status_code, headers)

    elif extension == "text":
        # Plain text response
        body = result.get("body", "")
        if isinstance(body, dict):
            body = json.dumps(body)
        return make_response(body, 200, {"Content-Type": "text/plain"})

    elif extension == "svg":
        # SVG image response
        body = result.get("body", "")
        if isinstance(body, dict):
            body = json.dumps(body)
        return make_response(body, 200, {"Content-Type": "image/svg+xml"})

    else:
        # Default to JSON
        return make_response(result, 200, {"Content-Type": "application/json"})


def _handle_cors_preflight() -> Response:
    """Handle CORS preflight OPTIONS request.

    Returns:
        Response with CORS headers
    """
    response = make_response("", 204)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS, HEAD"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


@web_bp.route("/web/<path:web_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def web_action(web_path: str) -> Response | tuple[dict[str, Any], int]:
    """Handle web action invocation.

    Web actions are public HTTP endpoints that don't require authentication.
    They receive HTTP request context as __ow_* parameters.

    Args:
        web_path: Path after /api/v1/web/ containing namespace/package/action.extension

    Returns:
        Response transformed based on extension or error dict with status code
    """
    # Handle CORS preflight
    if request.method == "OPTIONS":
        return _handle_cors_preflight()

    try:
        # Parse web action path
        namespace_name, package_name, action_name, extension = _parse_web_path(web_path)

    except ValueError as e:
        return {"error": str(e)}, 400

    # Get database connection
    db: DAL = get_db()

    try:
        # Look up namespace
        namespace = db(db.namespace.name == namespace_name).select().first()
        if not namespace:
            return {"error": f"Namespace '{namespace_name}' not found"}, 404

        # Look up action
        query = (db.action.namespace_id == namespace.id) & (db.action.name == action_name)

        # Handle package
        if package_name == "default":
            query &= db.action.package_id == None  # noqa: E711
        else:
            package = db(
                (db.package.namespace_id == namespace.id) & (db.package.name == package_name)
            ).select().first()
            if not package:
                return {"error": f"Package '{package_name}' not found"}, 404
            query &= db.action.package_id == package.id

        action = db(query).select().first()
        if not action:
            return {"error": f"Action '{action_name}' not found"}, 404

        # Verify web annotation is true
        annotations = action.annotations or []
        is_web = _get_annotation_value(annotations, "web-export", False)
        if not is_web:
            return {"error": f"Action '{action_name}' is not a web action"}, 403

        # Check require-whisk-auth annotation
        require_auth = _get_annotation_value(annotations, "require-whisk-auth", False)
        if require_auth:
            # Check for auth header or token
            auth_header = request.headers.get("X-Require-Whisk-Auth")
            if not auth_header or auth_header != str(require_auth):
                return {"error": "Authentication required"}, 401

        # Check raw-http annotation
        raw_http = _get_annotation_value(annotations, "raw-http", False)

        # Build __ow_* parameters
        ow_params = _build_ow_parameters(action_name, extension)

        # Get invocation service
        from app.services import get_invocation

        invocation_service = get_invocation()
        if not invocation_service:
            return {"error": "Invocation service unavailable"}, 503

        # Build action path
        if package_name == "default":
            action_path = action_name
        else:
            action_path = f"{package_name}/{action_name}"

        # Merge __ow_* parameters with request parameters
        merged_params = {**ow_params}

        # Get subject from authentication context if available
        subject = getattr(request, 'user', namespace_name)

        try:
            # Invoke action (always blocking for web actions)
            result = invocation_service.invoke_action(
                namespace=namespace_name,
                action_name=action_path,
                params=merged_params,
                blocking=True,
                result_only=True,  # Get only the result payload
                subject=subject
            )

            # Transform response based on extension
            response = _transform_response(result, extension)

            # Add CORS headers
            response.headers["Access-Control-Allow-Origin"] = "*"

            return response

        except ValueError as e:
            return {"error": f"Action not found: {str(e)}"}, 404
        except TimeoutError as e:
            return {"error": f"Action timed out: {str(e)}"}, 504
        except Exception as e:
            return {"error": f"Invocation error: {str(e)}"}, 500

    except Exception as e:
        return {"error": f"Internal server error: {str(e)}"}, 500
