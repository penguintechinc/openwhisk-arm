"""Flask application factory for PenguinWhisk Controller API."""

from __future__ import annotations

import logging
import time

from flask import Flask, g, jsonify, request
from flask_cors import CORS

from app.config import Config
from app.extensions import init_extensions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def create_app(config: type[Config] | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Configuration class to use. Defaults to Config from environment.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    # Load configuration
    if config is None:
        config = Config
    app.config.from_object(config)

    # Initialize extensions
    init_extensions(app)

    # Enable CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
            "expose_headers": ["Content-Type", "Authorization"],
            "supports_credentials": True,
            "max_age": 3600,
        }
    })

    # Register API blueprints
    from app.api import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    # Request logging middleware
    @app.before_request
    def log_request_info() -> None:
        """Log request information before processing."""
        g.start_time = time.time()
        logger.info(
            f"Request: {request.method} {request.path} "
            f"from {request.remote_addr}"
        )

    @app.after_request
    def log_response_info(response):
        """Log response information after processing."""
        if hasattr(g, 'start_time'):
            duration = time.time() - g.start_time
            logger.info(
                f"Response: {request.method} {request.path} "
                f"[{response.status_code}] in {duration:.3f}s"
            )
        return response

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health() -> dict[str, str]:
        """Health check endpoint.

        Returns:
            JSON response indicating service health.
        """
        return jsonify({"status": "healthy"})

    # Root endpoint
    @app.route("/", methods=["GET"])
    def root() -> dict[str, str]:
        """Root endpoint with API information.

        Returns:
            JSON response with API information.
        """
        return jsonify({
            "name": "PenguinWhisk Controller API",
            "version": "1.0.0",
            "endpoints": {
                "health": "/health",
                "api_v1": "/api/v1",
                "api_v1_health": "/api/v1/health",
            }
        })

    # Cleanup on app context teardown
    @app.teardown_appcontext
    def cleanup_services(error=None) -> None:
        """Cleanup services on app context teardown.

        Args:
            error: Exception if context is being torn down due to error
        """
        # Shutdown scheduler if running
        scheduler = app.extensions.get('scheduler')
        if scheduler and hasattr(scheduler, 'stop_monitoring'):
            scheduler.stop_monitoring()

        # Close messaging connection
        messaging = app.extensions.get('messaging')
        if messaging and hasattr(messaging, 'close'):
            messaging.close()

    return app
