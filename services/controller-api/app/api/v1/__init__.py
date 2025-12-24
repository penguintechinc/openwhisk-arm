"""OpenWhisk Controller API v1 blueprint.

Registers all v1 API endpoints for OpenWhisk compatibility.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from app.api.auth import AuthenticationError
from app.api.v1.actions import actions_bp
from app.api.v1.activations import activations_bp
from app.api.v1.namespaces import namespaces_bp
from app.api.v1.packages import packages_bp
from app.api.v1.rules import rules_bp
from app.api.v1.triggers import triggers_bp
from app.api.v1.web import web_bp
from app.utils.validators import ValidationError

# Create main API v1 blueprint
v1_bp = Blueprint("v1", __name__)

# Register sub-blueprints
v1_bp.register_blueprint(namespaces_bp)
v1_bp.register_blueprint(actions_bp)
v1_bp.register_blueprint(triggers_bp)
v1_bp.register_blueprint(rules_bp)
v1_bp.register_blueprint(packages_bp)
v1_bp.register_blueprint(activations_bp)
v1_bp.register_blueprint(web_bp)


# Error handlers
@v1_bp.errorhandler(ValidationError)
def handle_validation_error(error: ValidationError) -> tuple[dict[str, Any], int]:
    """Handle validation errors.

    Args:
        error: ValidationError instance.

    Returns:
        JSON response with error details and 400 status code.
    """
    return jsonify(error.to_dict()), 400


@v1_bp.errorhandler(AuthenticationError)
def handle_auth_error(error: AuthenticationError) -> tuple[dict[str, Any], int]:
    """Handle authentication errors.

    Args:
        error: AuthenticationError instance.

    Returns:
        JSON response with error details and 401 status code.
    """
    return jsonify(error.to_dict()), 401


@v1_bp.errorhandler(404)
def handle_not_found(error: HTTPException) -> tuple[dict[str, Any], int]:
    """Handle 404 Not Found errors.

    Args:
        error: HTTPException instance.

    Returns:
        JSON response with error details and 404 status code.
    """
    return jsonify({
        'error': 'Resource not found',
        'code': 404,
    }), 404


@v1_bp.errorhandler(500)
def handle_internal_error(error: HTTPException) -> tuple[dict[str, Any], int]:
    """Handle 500 Internal Server errors.

    Args:
        error: HTTPException instance.

    Returns:
        JSON response with error details and 500 status code.
    """
    return jsonify({
        'error': 'Internal server error',
        'code': 500,
    }), 500


@v1_bp.errorhandler(Exception)
def handle_generic_error(error: Exception) -> tuple[dict[str, Any], int]:
    """Handle generic exceptions.

    Args:
        error: Exception instance.

    Returns:
        JSON response with error details and 500 status code.
    """
    # Log the error
    import logging
    logging.exception('Unhandled exception in API v1')

    return jsonify({
        'error': 'An unexpected error occurred',
        'code': 500,
    }), 500


# Health check endpoint for v1
@v1_bp.route('/health', methods=['GET'])
def health() -> dict[str, str]:
    """Health check endpoint for API v1.

    Returns:
        JSON response indicating API v1 health status.
    """
    return jsonify({
        'status': 'healthy',
        'version': 'v1',
    })


__all__ = ["v1_bp"]
