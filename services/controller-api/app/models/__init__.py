"""
OpenWhisk Controller API Models Package.

Hybrid database architecture:
- SQLAlchemy: Flask-Security-Too auth models (User, Role)
- PyDAL: OpenWhisk entity models with auto-migrations

Thread-safe connection management and multi-database support.
"""

import threading
from typing import Optional
from pydal import DAL, Field
from flask import Flask, g

from .database import DatabaseManager, get_database_manager
from .sqlalchemy_models import db as sqlalchemy_db

__all__ = [
    # SQLAlchemy exports
    'db',
    # PyDAL exports
    'DAL',
    'Field',
    'get_db',
    'init_db',
    'define_tables',
    'DatabaseManager',
    'get_database_manager',
]

# SQLAlchemy database instance (for auth models)
db = sqlalchemy_db

# Thread-local storage for PyDAL connections
_thread_local = threading.local()


def get_db() -> DAL:
    """
    Get thread-local PyDAL database connection for OpenWhisk entities.

    Returns thread-local DAL instance, creating new connection if needed.
    Compatible with Flask's application context.

    Returns:
        DAL: Thread-local database connection
    """
    # Try Flask's g object first (if in Flask request context)
    if hasattr(g, 'pydal_db') and g.pydal_db is not None:
        return g.pydal_db

    # Fall back to thread-local storage
    if not hasattr(_thread_local, 'db') or _thread_local.db is None:
        db_manager = get_database_manager()
        _thread_local.db = db_manager.get_thread_connection()

    return _thread_local.db


def close_db(error: Optional[Exception] = None) -> None:
    """
    Close thread-local database connection.

    Registered as Flask teardown function to cleanup after each request.

    Args:
        error: Exception that occurred during request (if any)
    """
    pydal_db = g.pop('pydal_db', None)

    if pydal_db is not None:
        pydal_db.close()

    # Also cleanup thread-local storage
    if hasattr(_thread_local, 'db') and _thread_local.db is not None:
        _thread_local.db.close()
        _thread_local.db = None


def init_db(app: Flask) -> None:
    """
    Initialize hybrid database with Flask application.

    Sets up both SQLAlchemy (auth) and PyDAL (OpenWhisk entities).
    Defines tables and registers teardown handler.

    Args:
        app: Flask application instance
    """
    # Initialize SQLAlchemy for auth models
    db_manager = get_database_manager()
    app.config['SQLALCHEMY_DATABASE_URI'] = db_manager.build_connection_string(
        for_sqlalchemy=True
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    sqlalchemy_db.init_app(app)

    with app.app_context():
        # Create SQLAlchemy tables
        sqlalchemy_db.create_all()

        # Get PyDAL connection for OpenWhisk entities
        pydal_db = get_db()

        # Store in Flask's g object for request context
        g.pydal_db = pydal_db

        # Define all PyDAL tables
        define_tables(pydal_db)

        # Commit table definitions
        pydal_db.commit()

    # Register teardown function to close connections after each request
    app.teardown_appcontext(close_db)


def define_tables(db: DAL) -> None:
    """
    Define PyDAL tables for OpenWhisk entities only.

    SQLAlchemy handles User and Role tables separately.
    PyDAL handles:
    - Namespaces (multi-tenancy)
    - Actions (serverless functions)
    - Triggers (event sources)
    - Rules (trigger-action bindings)
    - Activations (execution records)
    - Packages (action grouping)

    Args:
        db: PyDAL DAL instance
    """
    # MariaDB Galera: Check if we need special handling
    db_manager = get_database_manager()
    is_galera = db_manager.is_galera and db_manager.db_type in ['mysql', 'mariadb']

    # 1. Define namespaces table (multi-tenancy, integer FK to SQLAlchemy user.id)
    db.define_table(
        'namespace',
        Field('name', 'string', length=255, unique=True, notnull=True),
        Field('owner_id', 'integer', notnull=True),  # FK to SQLAlchemy user.id
        Field('description', 'text'),
        Field('limits', 'json'),  # Resource limits as JSON
        Field('created_at', 'datetime', default='now'),
        Field('updated_at', 'datetime', update='now'),
        format='%(name)s',
    )

    # 2. Define packages table (action grouping, foreign key to namespace)
    db.define_table(
        'package',
        Field('name', 'string', length=255, notnull=True),
        Field('namespace_id', 'reference namespace', notnull=True, ondelete='CASCADE'),
        Field('version', 'string', length=50, default='0.0.1'),
        Field('publish', 'boolean', default=False),
        Field('annotations', 'json'),
        Field('parameters', 'json'),
        Field('created_at', 'datetime', default='now'),
        Field('updated_at', 'datetime', update='now'),
        format='%(name)s',
    )

    # 3. Define actions table (serverless functions, foreign key to namespace and package)
    db.define_table(
        'action',
        Field('name', 'string', length=255, notnull=True),
        Field('namespace_id', 'reference namespace', notnull=True, ondelete='CASCADE'),
        Field('package_id', 'reference package', ondelete='SET NULL'),
        Field('version', 'string', length=50, default='0.0.1'),
        Field('exec_kind', 'string', length=50, notnull=True),  # nodejs, python, go, etc.
        Field('exec_code', 'text', notnull=True),
        Field('exec_binary', 'boolean', default=False),
        Field('exec_main', 'string', length=255),
        Field('limits', 'json'),  # timeout, memory, logs
        Field('annotations', 'json'),
        Field('parameters', 'json'),
        Field('publish', 'boolean', default=False),
        Field('created_at', 'datetime', default='now'),
        Field('updated_at', 'datetime', update='now'),
        format='%(name)s',
    )

    # 4. Define triggers table (event sources, foreign key to namespace)
    db.define_table(
        'trigger',
        Field('name', 'string', length=255, notnull=True),
        Field('namespace_id', 'reference namespace', notnull=True, ondelete='CASCADE'),
        Field('version', 'string', length=50, default='0.0.1'),
        Field('annotations', 'json'),
        Field('parameters', 'json'),
        Field('publish', 'boolean', default=False),
        Field('created_at', 'datetime', default='now'),
        Field('updated_at', 'datetime', update='now'),
        format='%(name)s',
    )

    # 5. Define rules table (trigger-action bindings, foreign keys to trigger and action)
    db.define_table(
        'rule',
        Field('name', 'string', length=255, notnull=True),
        Field('namespace_id', 'reference namespace', notnull=True, ondelete='CASCADE'),
        Field('trigger_id', 'reference trigger', notnull=True, ondelete='CASCADE'),
        Field('action_id', 'reference action', notnull=True, ondelete='CASCADE'),
        Field('status', 'string', length=20, default='active'),  # active, inactive
        Field('version', 'string', length=50, default='0.0.1'),
        Field('created_at', 'datetime', default='now'),
        Field('updated_at', 'datetime', update='now'),
        format='%(name)s',
    )

    # 6. Define activations table (execution records, foreign key to action)
    # MariaDB Galera: Large auto-increment table - use special handling
    activation_fields = [
        Field('activation_id', 'string', length=64, unique=True, notnull=True),
        Field('namespace_id', 'reference namespace', notnull=True, ondelete='CASCADE'),
        Field('action_id', 'reference action', ondelete='SET NULL'),
        Field('cause', 'string', length=64),  # Parent activation ID
        Field('start', 'bigint', notnull=True),  # Epoch milliseconds
        Field('end', 'bigint'),  # Epoch milliseconds
        Field('duration', 'bigint'),  # Milliseconds
        Field('status_code', 'integer'),  # HTTP status code
        Field('response', 'json'),
        Field('logs', 'json'),
        Field('annotations', 'json'),
        Field('version', 'string', length=50),
        Field('publish', 'boolean', default=False),
        Field('created_at', 'datetime', default='now'),
    ]

    db.define_table('activation', *activation_fields)

    # Create indexes for better query performance
    # PyDAL doesn't have built-in index support, so we use executesql
    _create_indexes(db, is_galera)


def _create_indexes(db: DAL, is_galera: bool = False) -> None:
    """
    Create database indexes for PyDAL OpenWhisk entities.

    SQLAlchemy handles User/Role indexes separately.

    Args:
        db: PyDAL DAL instance
        is_galera: Whether database is MariaDB Galera cluster
    """
    # Get database type
    db_manager = get_database_manager()
    db_type = db_manager.db_type

    # Index definitions for OpenWhisk entities only
    indexes = [
        # Namespace indexes
        ('idx_namespace_name', 'namespace', ['name']),
        ('idx_namespace_owner', 'namespace', ['owner_id']),

        # Package indexes
        ('idx_package_name', 'package', ['name']),
        ('idx_package_namespace', 'package', ['namespace_id']),

        # Action indexes
        ('idx_action_name', 'action', ['name']),
        ('idx_action_namespace', 'action', ['namespace_id']),
        ('idx_action_package', 'action', ['package_id']),

        # Trigger indexes
        ('idx_trigger_name', 'trigger', ['name']),
        ('idx_trigger_namespace', 'trigger', ['namespace_id']),

        # Rule indexes
        ('idx_rule_name', 'rule', ['name']),
        ('idx_rule_namespace', 'rule', ['namespace_id']),
        ('idx_rule_trigger', 'rule', ['trigger_id']),
        ('idx_rule_action', 'rule', ['action_id']),

        # Activation indexes
        ('idx_activation_id', 'activation', ['activation_id']),
        ('idx_activation_namespace', 'activation', ['namespace_id']),
        ('idx_activation_action', 'activation', ['action_id']),
        ('idx_activation_start', 'activation', ['start']),
    ]

    # Create indexes based on database type
    for index_name, table_name, columns in indexes:
        try:
            if db_type in ['postgres', 'postgresql']:
                # PostgreSQL syntax
                cols = ', '.join(columns)
                db.executesql(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} ({cols})"
                )
            elif db_type in ['mysql', 'mariadb']:
                # MySQL/MariaDB syntax
                cols = ', '.join(columns)
                # Check if index exists first
                db.executesql(
                    f"CREATE INDEX {index_name} ON {table_name} ({cols})"
                )
            elif db_type == 'sqlite':
                # SQLite syntax
                cols = ', '.join(columns)
                db.executesql(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} ({cols})"
                )
        except Exception:
            # Index might already exist, skip
            pass

    db.commit()
