"""Authentication decorators and utilities for API endpoints.

Supports:
- API key authentication (Basic Auth format: uuid:key)
- JWT token authentication
- User context management
"""

from __future__ import annotations

import base64
import uuid
from functools import wraps
from typing import Any, Callable

from flask import g, jsonify, request
from flask_security import auth_token_required, current_user
from werkzeug.exceptions import Unauthorized

from app.models.sqlalchemy_models import User, db


class AuthenticationError(Unauthorized):
    """Custom authentication error exception."""

    def __init__(self, message: str = 'Authentication required'):
        """Initialize authentication error.

        Args:
            message: Error message.
        """
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format.

        Returns:
            Dictionary with error details.
        """
        return {
            'error': self.message,
            'code': 401,
        }


def extract_api_key_from_header() -> tuple[str | None, str | None]:
    """Extract API key from Authorization header.

    Supports Basic Auth format: Authorization: Basic base64(uuid:key)

    Returns:
        Tuple of (user_uuid, api_key) or (None, None) if not found.
    """
    auth_header = request.headers.get('Authorization', '')

    if not auth_header:
        return None, None

    # Parse Basic Auth
    if auth_header.startswith('Basic '):
        try:
            # Decode base64
            encoded = auth_header[6:]  # Remove 'Basic ' prefix
            decoded = base64.b64decode(encoded).decode('utf-8')

            # Split into username:password
            if ':' in decoded:
                user_uuid, api_key = decoded.split(':', 1)
                return user_uuid, api_key
        except Exception:
            pass

    return None, None


def validate_api_key(user_uuid: str, api_key: str) -> User | None:
    """Validate API key against database.

    Args:
        user_uuid: User UUID (fs_uniquifier).
        api_key: API key to validate.

    Returns:
        User object if valid, None otherwise.
    """
    try:
        # Validate UUID format
        uuid.UUID(user_uuid)
    except ValueError:
        return None

    # Query user by fs_uniquifier (UUID) and api_key
    user = db.session.query(User).filter_by(
        fs_uniquifier=user_uuid,
        api_key=api_key,
        active=True,
    ).first()

    return user


def require_api_key(func: Callable) -> Callable:
    """Decorator to require API key authentication.

    Extracts API key from Authorization header (Basic Auth format).
    Validates against database and sets current user context.

    Usage:
        @app.route('/api/v1/namespaces')
        @require_api_key
        def list_namespaces():
            # Access current user via g.current_user
            return jsonify({'user': g.current_user.email})

    Raises:
        AuthenticationError: If authentication fails.
    """
    @wraps(func)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        # Extract API key from header
        user_uuid, api_key = extract_api_key_from_header()

        if not user_uuid or not api_key:
            raise AuthenticationError('API key required')

        # Validate API key
        user = validate_api_key(user_uuid, api_key)

        if not user:
            raise AuthenticationError('Invalid API key')

        # Set current user in request context
        g.current_user = user
        g.user_id = user.id

        return func(*args, **kwargs)

    return decorated_function


def require_auth(func: Callable) -> Callable:
    """Decorator to require authentication (JWT or API key).

    Supports both JWT tokens and API keys.
    Tries JWT first, falls back to API key.

    Usage:
        @app.route('/api/v1/actions')
        @require_auth
        def list_actions():
            # Access current user via g.current_user
            return jsonify({'user': g.current_user.email})

    Raises:
        AuthenticationError: If authentication fails.
    """
    @wraps(func)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        # Try JWT authentication first
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            try:
                # Use Flask-Security's JWT authentication
                @auth_token_required
                def jwt_auth() -> Any:
                    g.current_user = current_user._get_current_object()
                    g.user_id = current_user.id
                    return func(*args, **kwargs)

                return jwt_auth()
            except Exception:
                pass

        # Fall back to API key authentication
        user_uuid, api_key = extract_api_key_from_header()

        if not user_uuid or not api_key:
            raise AuthenticationError('Authentication required (JWT or API key)')

        # Validate API key
        user = validate_api_key(user_uuid, api_key)

        if not user:
            raise AuthenticationError('Invalid credentials')

        # Set current user in request context
        g.current_user = user
        g.user_id = user.id

        return func(*args, **kwargs)

    return decorated_function


def get_current_user() -> User | None:
    """Get current authenticated user.

    Returns:
        User object if authenticated, None otherwise.
    """
    return getattr(g, 'current_user', None)


def get_current_user_id() -> int | None:
    """Get current authenticated user ID.

    Returns:
        User ID if authenticated, None otherwise.
    """
    return getattr(g, 'user_id', None)


def require_role(role_name: str) -> Callable:
    """Decorator to require specific role.

    Args:
        role_name: Required role name (e.g., 'Admin', 'Maintainer').

    Usage:
        @app.route('/api/v1/admin')
        @require_auth
        @require_role('Admin')
        def admin_endpoint():
            return jsonify({'message': 'Admin only'})

    Raises:
        AuthenticationError: If user doesn't have required role.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            user = get_current_user()

            if not user:
                raise AuthenticationError('Authentication required')

            if not user.has_role(role_name):
                raise AuthenticationError(
                    f'Insufficient permissions: {role_name} role required'
                )

            return func(*args, **kwargs)

        return decorated_function

    return decorator
