"""
Database manager with hybrid SQLAlchemy + PyDAL support.

Architecture:
- SQLAlchemy: Flask-Security-Too auth models (User, Role)
- PyDAL: OpenWhisk entities (Namespace, Action, Trigger, Rule, Package, Activation)

Both use same underlying database via DB_TYPE environment variable.
Thread-safe connection handling with connection pooling.
MariaDB Galera cluster support with WSREP considerations.
"""

import os
import threading
from typing import Optional, Dict, Any
from pydal import DAL, Field
from flask_sqlalchemy import SQLAlchemy


class DatabaseManager:
    """
    Manages hybrid SQLAlchemy + PyDAL database connections.

    SQLAlchemy: Flask-Security-Too auth models
    PyDAL: OpenWhisk entity models with auto-migrations

    Supports all PyDAL database types and handles connection lifecycle.
    Thread-local storage ensures safe concurrent access.
    """

    _instance: Optional['DatabaseManager'] = None
    _lock: threading.Lock = threading.Lock()
    _local: threading.local = threading.local()

    # Supported database types mapping to PyDAL connection string prefixes
    DB_TYPE_MAP: Dict[str, str] = {
        'postgres': 'postgres://',
        'postgresql': 'postgres://',
        'mysql': 'mysql://',
        'mariadb': 'mysql://',  # MariaDB uses MySQL adapter
        'sqlite': 'sqlite://',
        'mssql': 'mssql://',
        'oracle': 'oracle://',
        'db2': 'db2://',
        'teradata': 'teradata://',
        'ingres': 'ingres://',
        'informix': 'informix://',
        'firebird': 'firebird://',
        'mongodb': 'mongodb://',
        'imap': 'imap://',
        'google:sql': 'google:sql://',
        'google:datastore': 'google:datastore',
        'cubrid': 'cubrid://',
        'sapdb': 'sapdb://',
        'sybase': 'sybase://',
    }

    def __new__(cls) -> 'DatabaseManager':
        """Singleton pattern implementation."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize database manager with configuration."""
        if not hasattr(self, '_initialized'):
            self.db_type: str = os.getenv('DB_TYPE', 'postgres').lower()
            self.db_host: str = os.getenv('DB_HOST', 'localhost')
            self.db_port: int = int(os.getenv('DB_PORT', self._get_default_port()))
            self.db_name: str = os.getenv('DB_NAME', 'openwhisk')
            self.db_user: str = os.getenv('DB_USER', 'openwhisk')
            self.db_password: str = os.getenv('DB_PASSWORD', 'openwhisk')
            self.pool_size: int = int(os.getenv('DB_POOL_SIZE', '10'))
            self.migrate: bool = os.getenv('DB_MIGRATE', 'true').lower() == 'true'
            self.fake_migrate: bool = os.getenv('DB_FAKE_MIGRATE', 'false').lower() == 'true'
            self.lazy_tables: bool = os.getenv('DB_LAZY_TABLES', 'false').lower() == 'true'

            # MariaDB Galera specific settings
            self.is_galera: bool = os.getenv('DB_GALERA', 'false').lower() == 'true'
            self.galera_auto_increment_offset: int = int(os.getenv('DB_GALERA_AUTO_INCREMENT_OFFSET', '1'))
            self.galera_auto_increment_increment: int = int(os.getenv('DB_GALERA_AUTO_INCREMENT_INCREMENT', '1'))

            self._validate_db_type()
            self._initialized = True

    def _get_default_port(self) -> str:
        """Get default port for database type."""
        port_map: Dict[str, str] = {
            'postgres': '5432',
            'postgresql': '5432',
            'mysql': '3306',
            'mariadb': '3306',
            'sqlite': '0',
            'mssql': '1433',
            'oracle': '1521',
            'mongodb': '27017',
        }
        return port_map.get(self.db_type, '5432')

    def _validate_db_type(self) -> None:
        """Validate DB_TYPE is supported by PyDAL."""
        if self.db_type not in self.DB_TYPE_MAP:
            supported: str = ', '.join(sorted(self.DB_TYPE_MAP.keys()))
            raise ValueError(
                f"Unsupported DB_TYPE: {self.db_type}. "
                f"Supported types: {supported}"
            )

    def build_connection_string(self, for_sqlalchemy: bool = False) -> str:
        """
        Build database connection string based on DB_TYPE.

        Args:
            for_sqlalchemy: If True, return SQLAlchemy format; else PyDAL format.

        Returns:
            Connection string for SQLAlchemy or PyDAL
        """
        # SQLAlchemy uses different prefix format
        if for_sqlalchemy:
            if self.db_type == 'sqlite':
                db_path: str = os.getenv('DB_PATH', 'db/openwhisk.db')
                return f"sqlite:///{db_path}"
            elif self.db_type in ['postgres', 'postgresql']:
                return (
                    f"postgresql://{self.db_user}:{self.db_password}@"
                    f"{self.db_host}:{self.db_port}/{self.db_name}"
                )
            elif self.db_type in ['mysql', 'mariadb']:
                return (
                    f"mysql+pymysql://{self.db_user}:{self.db_password}@"
                    f"{self.db_host}:{self.db_port}/{self.db_name}"
                )
            else:
                # Fallback for other types
                prefix: str = self.DB_TYPE_MAP[self.db_type].rstrip('://')
                return (
                    f"{prefix}://{self.db_user}:{self.db_password}@"
                    f"{self.db_host}:{self.db_port}/{self.db_name}"
                )

        # PyDAL connection string format
        prefix = self.DB_TYPE_MAP[self.db_type]

        # Special cases for databases without network connections
        if self.db_type == 'sqlite':
            db_path = os.getenv('DB_PATH', 'db/openwhisk.db')
            return f"sqlite://{db_path}"

        if self.db_type in ['google:datastore']:
            return prefix

        # Standard network database connection string
        if self.db_type in ['postgres', 'postgresql']:
            return (
                f"{prefix}{self.db_user}:{self.db_password}@"
                f"{self.db_host}:{self.db_port}/{self.db_name}"
            )

        if self.db_type in ['mysql', 'mariadb']:
            return (
                f"{prefix}{self.db_user}:{self.db_password}@"
                f"{self.db_host}:{self.db_port}/{self.db_name}"
            )

        if self.db_type == 'mssql':
            return (
                f"{prefix}{self.db_user}:{self.db_password}@"
                f"{self.db_host}:{self.db_port}/{self.db_name}"
            )

        if self.db_type == 'oracle':
            return (
                f"{prefix}{self.db_user}:{self.db_password}@"
                f"{self.db_host}:{self.db_port}/{self.db_name}"
            )

        if self.db_type == 'mongodb':
            return (
                f"{prefix}{self.db_user}:{self.db_password}@"
                f"{self.db_host}:{self.db_port}/{self.db_name}"
            )

        # Generic fallback for other database types
        return (
            f"{prefix}{self.db_user}:{self.db_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def get_connection_params(self) -> Dict[str, Any]:
        """
        Get additional connection parameters for DAL.

        Returns:
            Dictionary of DAL constructor parameters
        """
        params: Dict[str, Any] = {
            'pool_size': self.pool_size,
            'migrate': self.migrate,
            'fake_migrate': self.fake_migrate,
            'lazy_tables': self.lazy_tables,
            'check_reserved': ['all'],
        }

        # Add folder for migration files
        params['folder'] = os.getenv('DB_MIGRATIONS_FOLDER', 'migrations')

        # MariaDB Galera specific connection parameters
        if self.is_galera and self.db_type in ['mysql', 'mariadb']:
            params['driver_args'] = {
                'init_command': (
                    f"SET SESSION wsrep_sync_wait=1; "
                    f"SET SESSION auto_increment_offset={self.galera_auto_increment_offset}; "
                    f"SET SESSION auto_increment_increment={self.galera_auto_increment_increment};"
                )
            }

        return params

    def create_connection(self) -> DAL:
        """
        Create new PyDAL database connection.

        Returns:
            DAL instance with active connection
        """
        uri: str = self.build_connection_string()
        params: Dict[str, Any] = self.get_connection_params()

        db = DAL(uri, **params)

        # MariaDB Galera runtime settings
        if self.is_galera and self.db_type in ['mysql', 'mariadb']:
            db.executesql("SET SESSION wsrep_sync_wait=1")
            db.executesql(f"SET SESSION auto_increment_offset={self.galera_auto_increment_offset}")
            db.executesql(f"SET SESSION auto_increment_increment={self.galera_auto_increment_increment}")

        return db

    def get_thread_connection(self) -> DAL:
        """
        Get thread-local database connection.

        Creates new connection if none exists for current thread.

        Returns:
            Thread-local DAL instance
        """
        if not hasattr(self._local, 'db') or self._local.db is None:
            self._local.db = self.create_connection()
        return self._local.db

    def close_thread_connection(self) -> None:
        """Close and cleanup thread-local database connection."""
        if hasattr(self._local, 'db') and self._local.db is not None:
            self._local.db.close()
            self._local.db = None

    def cleanup_all_connections(self) -> None:
        """
        Cleanup all database connections.

        Warning: Only call during application shutdown.
        """
        self.close_thread_connection()

    def execute_migration(self, migration_sql: str) -> None:
        """
        Execute database migration SQL.

        Args:
            migration_sql: SQL statements to execute
        """
        db = self.get_thread_connection()

        # MariaDB Galera: Ensure wsrep_sync_wait before DDL
        if self.is_galera and self.db_type in ['mysql', 'mariadb']:
            db.executesql("SET SESSION wsrep_sync_wait=1")

        db.executesql(migration_sql)
        db.commit()


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_database_manager() -> DatabaseManager:
    """
    Get global database manager instance.

    Returns:
        Singleton DatabaseManager instance
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager
