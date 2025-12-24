"""OpenWhisk Controller API package.

Registers all API blueprints and versions.
"""

from __future__ import annotations

from flask import Blueprint

from app.api.v1 import v1_bp

# Create main API blueprint
api_bp = Blueprint('api', __name__)

# Register versioned blueprints
api_bp.register_blueprint(v1_bp, url_prefix='/v1')

__all__ = ['api_bp']
