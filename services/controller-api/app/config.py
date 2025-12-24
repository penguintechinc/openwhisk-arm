"""Flask configuration management for PenguinWhisk Controller API."""

from __future__ import annotations

import os
from datetime import timedelta


class Config:
    """Base configuration class loading from environment variables."""

    # Flask settings
    DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    TESTING = os.getenv("FLASK_TESTING", "False").lower() == "true"
    ENV = os.getenv("FLASK_ENV", "production")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

    # Database configuration
    DB_TYPE = os.getenv("DB_TYPE", "postgres")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_NAME = os.getenv("DB_NAME", "penguinwhisk")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    # Database URI mapping for PyDAL
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    # Redis configuration
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    REDIS_SSL = os.getenv("REDIS_SSL", "False").lower() == "true"

    # MinIO configuration
    MINIO_HOST = os.getenv("MINIO_HOST", "localhost")
    MINIO_PORT = int(os.getenv("MINIO_PORT", "9000"))
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_BUCKET = os.getenv("MINIO_BUCKET", "penguinwhisk")
    MINIO_SSL = os.getenv("MINIO_SSL", "False").lower() == "true"

    # JWT configuration
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "jwt-secret-key-change-in-production")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))

    # Flask-Security-Too configuration
    SECURITY_PASSWORD_SALT = os.getenv("SECURITY_PASSWORD_SALT", "security-salt-change-in-production")
    SECURITY_PASSWORD_SCHEMES = ["bcrypt", "argon2"]
    SECURITY_DEPRECATED_PASSWORD_SCHEMES = []
    SECURITY_BCRYPT_LOG_ROUNDS = 12
    SECURITY_PASSWORD_MIN_LENGTH = 8

    # Session configuration
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "False").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # License server configuration
    LICENSE_SERVER_URL = os.getenv("LICENSE_SERVER_URL", "https://license.penguintech.io")
    PRODUCT_NAME = os.getenv("PRODUCT_NAME", "penguinwhisk-controller")
    LICENSE_KEY = os.getenv("LICENSE_KEY", "")
    RELEASE_MODE = os.getenv("RELEASE_MODE", "False").lower() == "true"

    # Logging configuration
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    def get_database_uri(self) -> str:
        """Generate PyDAL-compatible database URI.

        Returns:
            Database connection URI for PyDAL.

        Raises:
            ValueError: If DB_TYPE is unsupported.
        """
        db_type = self.DB_TYPE.lower()

        if db_type == "postgres":
            return (
                f"postgres://{self.DB_USER}:{self.DB_PASSWORD}@"
                f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        elif db_type == "mysql":
            return (
                f"mysql://{self.DB_USER}:{self.DB_PASSWORD}@"
                f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        elif db_type == "sqlite":
            return f"sqlite:{self.DB_NAME}"
        else:
            raise ValueError(f"Unsupported database type: {db_type}")


class DevelopmentConfig(Config):
    """Development environment configuration."""

    DEBUG = True
    TESTING = False
    ENV = "development"
    SECURITY_PASSWORD_SCHEMES = ["plaintext"]


class TestingConfig(Config):
    """Testing environment configuration."""

    DEBUG = True
    TESTING = True
    ENV = "testing"
    SECRET_KEY = "test-secret-key"
    JWT_SECRET_KEY = "test-jwt-secret"
    SECURITY_PASSWORD_SALT = "test-salt"
    DB_NAME = "penguinwhisk_test"
    REDIS_DB = 1


class ProductionConfig(Config):
    """Production environment configuration."""

    DEBUG = False
    TESTING = False
    ENV = "production"
