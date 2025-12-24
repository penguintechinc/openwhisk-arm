"""
MinIO Storage Service for OpenWhisk Action Code Blobs.

This module provides a storage service for managing action code artifacts
using MinIO object storage. It handles code upload, retrieval, deletion,
and presigned URL generation for the OpenWhisk controller API.
"""

import hashlib
import logging
import os
from typing import Optional

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Custom exception for storage-related errors."""

    pass


class StorageService:
    """
    MinIO-based storage service for action code blobs.

    This service manages the lifecycle of action code artifacts in MinIO,
    including storage, retrieval, deletion, and presigned URL generation
    for efficient code distribution to invokers.

    Attributes:
        client: MinIO client instance
        bucket: Bucket name for storing action code
        max_retries: Maximum number of retry attempts for transient failures
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = True,
        bucket: str = "actions",
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the MinIO storage service.

        Args:
            endpoint: MinIO server endpoint (host:port)
            access_key: MinIO access key
            secret_key: MinIO secret key
            secure: Use HTTPS for connections (default: True)
            bucket: Bucket name for action storage (default: 'actions')
            max_retries: Maximum retry attempts for transient failures

        Raises:
            StorageError: If client initialization or bucket creation fails
        """
        self.bucket = bucket
        self.max_retries = max_retries

        try:
            self.client = Minio(
                endpoint, access_key=access_key, secret_key=secret_key, secure=secure
            )
            self._ensure_bucket_exists()
            logger.info(
                f"MinIO storage service initialized: endpoint={endpoint}, "
                f"bucket={bucket}, secure={secure}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize MinIO client: {e}")
            raise StorageError(f"MinIO initialization failed: {e}") from e

    def _ensure_bucket_exists(self) -> None:
        """
        Ensure the storage bucket exists, creating it if necessary.

        Raises:
            StorageError: If bucket check or creation fails
        """
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"Created MinIO bucket: {self.bucket}")
            else:
                logger.debug(f"MinIO bucket already exists: {self.bucket}")
        except S3Error as e:
            logger.error(f"Failed to ensure bucket exists: {e}")
            raise StorageError(f"Bucket creation failed: {e}") from e

    def _generate_code_hash(self, code: bytes) -> str:
        """
        Generate SHA256 hash of code bytes.

        Args:
            code: Action code as bytes

        Returns:
            Hexadecimal SHA256 hash string
        """
        return hashlib.sha256(code).hexdigest()

    def _get_object_path(
        self, namespace: str, action_name: str, code_hash: str
    ) -> str:
        """
        Generate MinIO object path for action code.

        Args:
            namespace: Action namespace
            action_name: Action name
            code_hash: SHA256 hash of code

        Returns:
            Object path string: actions/{namespace}/{action_name}/{code_hash}
        """
        return f"actions/{namespace}/{action_name}/{code_hash}"

    def _retry_operation(self, operation, *args, **kwargs):
        """
        Retry an operation with exponential backoff for transient failures.

        Args:
            operation: Function to execute
            *args: Positional arguments for operation
            **kwargs: Keyword arguments for operation

        Returns:
            Result of successful operation

        Raises:
            StorageError: If all retry attempts fail
        """
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                return operation(*args, **kwargs)
            except (S3Error, MaxRetryError) as e:
                last_exception = e
                logger.warning(
                    f"Storage operation failed (attempt {attempt + 1}/"
                    f"{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    break
                # Could add exponential backoff sleep here if needed

        logger.error(f"Storage operation failed after {self.max_retries} attempts")
        raise StorageError(
            f"Operation failed after {self.max_retries} retries: {last_exception}"
        ) from last_exception

    def store_action_code(
        self, namespace: str, action_name: str, code: bytes, binary: bool = False
    ) -> str:
        """
        Store action code in MinIO and return code hash.

        Args:
            namespace: Action namespace
            action_name: Action name
            code: Action code as bytes
            binary: Whether code is binary (affects content-type metadata)

        Returns:
            SHA256 hash of stored code

        Raises:
            StorageError: If storage operation fails
        """
        code_hash = self._generate_code_hash(code)
        object_path = self._get_object_path(namespace, action_name, code_hash)

        content_type = "application/octet-stream" if binary else "text/plain"

        def _store():
            from io import BytesIO

            self.client.put_object(
                self.bucket,
                object_path,
                BytesIO(code),
                length=len(code),
                content_type=content_type,
            )

        try:
            self._retry_operation(_store)
            logger.info(
                f"Stored action code: {object_path} (hash={code_hash}, "
                f"size={len(code)}, binary={binary})"
            )
            return code_hash
        except StorageError as e:
            logger.error(f"Failed to store action code: {e}")
            raise

    def get_action_code(
        self, namespace: str, action_name: str, code_hash: str
    ) -> bytes:
        """
        Retrieve action code from MinIO.

        Args:
            namespace: Action namespace
            action_name: Action name
            code_hash: SHA256 hash of code to retrieve

        Returns:
            Action code as bytes

        Raises:
            StorageError: If retrieval fails or object not found
        """
        object_path = self._get_object_path(namespace, action_name, code_hash)

        def _get():
            response = self.client.get_object(self.bucket, object_path)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            code = self._retry_operation(_get)
            logger.info(
                f"Retrieved action code: {object_path} (hash={code_hash}, "
                f"size={len(code)})"
            )
            return code
        except StorageError as e:
            logger.error(f"Failed to retrieve action code: {e}")
            raise

    def delete_action_code(
        self, namespace: str, action_name: str, code_hash: str
    ) -> bool:
        """
        Delete action code from MinIO.

        Args:
            namespace: Action namespace
            action_name: Action name
            code_hash: SHA256 hash of code to delete

        Returns:
            True if deletion successful

        Raises:
            StorageError: If deletion fails
        """
        object_path = self._get_object_path(namespace, action_name, code_hash)

        def _delete():
            self.client.remove_object(self.bucket, object_path)

        try:
            self._retry_operation(_delete)
            logger.info(f"Deleted action code: {object_path} (hash={code_hash})")
            return True
        except StorageError as e:
            logger.error(f"Failed to delete action code: {e}")
            raise

    def get_code_url(
        self, namespace: str, action_name: str, code_hash: str, expires: int = 3600
    ) -> str:
        """
        Generate presigned URL for action code retrieval.

        This URL can be used by invokers to fetch action code directly
        from MinIO without going through the controller API.

        Args:
            namespace: Action namespace
            action_name: Action name
            code_hash: SHA256 hash of code
            expires: URL expiration time in seconds (default: 3600)

        Returns:
            Presigned URL string

        Raises:
            StorageError: If URL generation fails
        """
        object_path = self._get_object_path(namespace, action_name, code_hash)

        def _get_url():
            from datetime import timedelta

            return self.client.presigned_get_object(
                self.bucket, object_path, expires=timedelta(seconds=expires)
            )

        try:
            url = self._retry_operation(_get_url)
            logger.debug(
                f"Generated presigned URL: {object_path} (expires={expires}s)"
            )
            return url
        except StorageError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise


def create_storage_service() -> StorageService:
    """
    Create StorageService instance from environment variables.

    Required environment variables:
        - MINIO_ENDPOINT: MinIO server endpoint (host:port)
        - MINIO_ACCESS_KEY: MinIO access key
        - MINIO_SECRET_KEY: MinIO secret key

    Optional environment variables:
        - MINIO_SECURE: Use HTTPS (default: 'true')
        - MINIO_BUCKET: Bucket name (default: 'actions')
        - MINIO_MAX_RETRIES: Maximum retry attempts (default: '3')

    Returns:
        Configured StorageService instance

    Raises:
        ValueError: If required environment variables are missing
        StorageError: If service initialization fails
    """
    endpoint = os.getenv("MINIO_ENDPOINT")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")

    if not all([endpoint, access_key, secret_key]):
        raise ValueError(
            "Missing required MinIO configuration: MINIO_ENDPOINT, "
            "MINIO_ACCESS_KEY, and MINIO_SECRET_KEY must be set"
        )

    secure = os.getenv("MINIO_SECURE", "true").lower() in ("true", "1", "yes")
    bucket = os.getenv("MINIO_BUCKET", "actions")
    max_retries = int(os.getenv("MINIO_MAX_RETRIES", "3"))

    return StorageService(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        bucket=bucket,
        max_retries=max_retries,
    )
