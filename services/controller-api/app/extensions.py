"""Flask extension initialization for PenguinWhisk Controller API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask_security import Security, SQLAlchemyUserDatastore

if TYPE_CHECKING:
    from flask import Flask


# Extension instances - to be initialized in create_app()
# Cache
redis_client = None  # Redis client

# Authentication
security = Security()

# MinIO
minio_client = None  # MinIO client


def init_storage(app: Flask) -> None:
    """Initialize storage service.

    Args:
        app: Flask application instance.
    """
    try:
        from app.services.storage import create_storage_service

        storage = create_storage_service()
        app.extensions['storage'] = storage
        app.logger.info("Storage service initialized successfully")

    except Exception as e:
        app.logger.error(f"Failed to initialize storage service: {e}")
        app.extensions['storage'] = None


def init_messaging(app: Flask) -> None:
    """Initialize messaging service.

    Args:
        app: Flask application instance.
    """
    try:
        from app.services.messaging import MessagingService

        messaging = MessagingService()
        app.extensions['messaging'] = messaging
        app.logger.info("Messaging service initialized successfully")

    except Exception as e:
        app.logger.error(f"Failed to initialize messaging service: {e}")
        app.extensions['messaging'] = None


def init_scheduler(app: Flask) -> None:
    """Initialize scheduler service.

    Args:
        app: Flask application instance.
    """
    try:
        from app.services.scheduler import SchedulerService

        messaging = app.extensions.get('messaging')
        if not messaging:
            app.logger.warning("Messaging service not available for scheduler")
            app.extensions['scheduler'] = None
            return

        scheduler = SchedulerService(messaging)
        scheduler.start_monitoring()
        app.extensions['scheduler'] = scheduler
        app.logger.info("Scheduler service initialized successfully")

    except Exception as e:
        app.logger.error(f"Failed to initialize scheduler service: {e}")
        app.extensions['scheduler'] = None


def init_invocation(app: Flask) -> None:
    """Initialize invocation service.

    Args:
        app: Flask application instance.
    """
    try:
        from app.services.invocation import InvocationService

        invocation = InvocationService()
        invocation.init_app(app)
        app.extensions['invocation'] = invocation
        app.logger.info("Invocation service initialized successfully")

    except Exception as e:
        app.logger.error(f"Failed to initialize invocation service: {e}")
        app.extensions['invocation'] = None


def init_extensions(app: Flask) -> None:
    """Initialize Flask extensions.

    Hybrid database architecture:
    - SQLAlchemy: Flask-Security-Too auth models (User, Role)
    - PyDAL: OpenWhisk entities with auto-migrations

    Args:
        app: Flask application instance.
    """
    # Import models and database instances
    from app.models import db, init_db
    from app.models.sqlalchemy_models import User, Role

    # Initialize hybrid database (SQLAlchemy + PyDAL)
    init_db(app)

    # Initialize Flask-Security-Too with SQLAlchemy datastore
    user_datastore = SQLAlchemyUserDatastore(db, User, Role)
    security.init_app(app, user_datastore)

    # Initialize services
    init_storage(app)
    init_messaging(app)
    init_scheduler(app)
    init_invocation(app)

    # TODO: Initialize Redis client
    # global redis_client
    # import redis
    # redis_client = redis.Redis(
    #     host=app.config['REDIS_HOST'],
    #     port=app.config['REDIS_PORT'],
    #     db=app.config['REDIS_DB'],
    #     password=app.config['REDIS_PASSWORD'],
    #     ssl=app.config['REDIS_SSL'],
    #     decode_responses=True
    # )

    # TODO: Initialize MinIO client
    # global minio_client
    # from minio import Minio
    # minio_client = Minio(
    #     f"{app.config['MINIO_HOST']}:{app.config['MINIO_PORT']}",
    #     access_key=app.config['MINIO_ACCESS_KEY'],
    #     secret_key=app.config['MINIO_SECRET_KEY'],
    #     secure=app.config['MINIO_SSL']
    # )
