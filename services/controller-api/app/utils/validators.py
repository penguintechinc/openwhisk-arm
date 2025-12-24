"""Validation utilities for OpenWhisk entities.

Implements OpenWhisk naming rules and resource limits validation.
"""

from __future__ import annotations

import re
from typing import Any

from flask import jsonify
from werkzeug.exceptions import BadRequest

# OpenWhisk naming rules
ENTITY_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_@.\-]+$')
MAX_NAME_LENGTH = 256
MAX_ACTION_CODE_SIZE = 48 * 1024 * 1024  # 48 MB
MAX_PARAMETER_SIZE = 1 * 1024 * 1024  # 1 MB

# Valid exec kinds
VALID_EXEC_KINDS = {
    'nodejs:18',
    'nodejs:20',
    'python:3.9',
    'python:3.10',
    'python:3.11',
    'python:3.12',
    'python:3.13',
    'go:1.21',
    'go:1.22',
    'go:1.23',
    'java:11',
    'java:17',
    'java:21',
    'php:8.1',
    'php:8.2',
    'ruby:3.2',
    'ruby:3.3',
    'swift:5.9',
    'rust:1.75',
    'blackbox',  # Custom Docker image
}


class ValidationError(BadRequest):
    """Custom validation error exception."""

    def __init__(self, message: str, field: str | None = None):
        """Initialize validation error.

        Args:
            message: Error message.
            field: Field name that failed validation.
        """
        super().__init__(message)
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format.

        Returns:
            Dictionary with error details.
        """
        result = {
            'error': self.message,
        }
        if self.field:
            result['field'] = self.field
        return result


def validate_entity_name(name: str, field: str = 'name') -> None:
    """Validate OpenWhisk entity name.

    OpenWhisk naming rules:
    - Must match pattern: [a-zA-Z0-9_@.-]+
    - Maximum length: 256 characters
    - Cannot be empty

    Args:
        name: Entity name to validate.
        field: Field name for error messages.

    Raises:
        ValidationError: If name is invalid.
    """
    if not name:
        raise ValidationError(f'{field} cannot be empty', field=field)

    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(
            f'{field} exceeds maximum length of {MAX_NAME_LENGTH} characters',
            field=field,
        )

    if not ENTITY_NAME_PATTERN.match(name):
        raise ValidationError(
            f'{field} must contain only letters, numbers, and characters: _ @ . -',
            field=field,
        )


def validate_action_code(code: str, binary: bool = False) -> None:
    """Validate action code size.

    Args:
        code: Action code content.
        binary: Whether code is binary.

    Raises:
        ValidationError: If code size exceeds limit.
    """
    if not code:
        raise ValidationError('Action code cannot be empty', field='exec.code')

    code_size = len(code.encode('utf-8'))
    if code_size > MAX_ACTION_CODE_SIZE:
        size_mb = code_size / (1024 * 1024)
        max_mb = MAX_ACTION_CODE_SIZE / (1024 * 1024)
        raise ValidationError(
            f'Action code size ({size_mb:.2f} MB) exceeds '
            f'maximum size of {max_mb} MB',
            field='exec.code',
        )


def validate_exec_kind(kind: str) -> None:
    """Validate action exec kind.

    Args:
        kind: Exec kind (e.g., 'nodejs:18', 'python:3.11').

    Raises:
        ValidationError: If kind is not supported.
    """
    if not kind:
        raise ValidationError('Exec kind cannot be empty', field='exec.kind')

    if kind not in VALID_EXEC_KINDS:
        raise ValidationError(
            f'Unsupported exec kind: {kind}. Valid kinds: '
            f'{", ".join(sorted(VALID_EXEC_KINDS))}',
            field='exec.kind',
        )


def validate_parameters(parameters: dict[str, Any]) -> None:
    """Validate parameter size.

    Args:
        parameters: Parameter dictionary.

    Raises:
        ValidationError: If parameter size exceeds limit.
    """
    if not parameters:
        return

    import json

    param_size = len(json.dumps(parameters).encode('utf-8'))
    if param_size > MAX_PARAMETER_SIZE:
        size_kb = param_size / 1024
        max_kb = MAX_PARAMETER_SIZE / 1024
        raise ValidationError(
            f'Parameter size ({size_kb:.2f} KB) exceeds '
            f'maximum size of {max_kb} KB',
            field='parameters',
        )


def validate_annotations(annotations: dict[str, Any]) -> None:
    """Validate annotation size.

    Args:
        annotations: Annotation dictionary.

    Raises:
        ValidationError: If annotation size exceeds limit.
    """
    if not annotations:
        return

    import json

    annotation_size = len(json.dumps(annotations).encode('utf-8'))
    if annotation_size > MAX_PARAMETER_SIZE:
        size_kb = annotation_size / 1024
        max_kb = MAX_PARAMETER_SIZE / 1024
        raise ValidationError(
            f'Annotation size ({size_kb:.2f} KB) exceeds '
            f'maximum size of {max_kb} KB',
            field='annotations',
        )


def validate_limits(limits: dict[str, Any]) -> None:
    """Validate resource limits.

    Args:
        limits: Limits dictionary (timeout, memory, logs).

    Raises:
        ValidationError: If limits are invalid.
    """
    if not limits:
        return

    # Validate timeout (milliseconds)
    if 'timeout' in limits:
        timeout = limits['timeout']
        if not isinstance(timeout, int) or timeout < 100 or timeout > 600000:
            raise ValidationError(
                'Timeout must be between 100ms and 600000ms (10 minutes)',
                field='limits.timeout',
            )

    # Validate memory (MB)
    if 'memory' in limits:
        memory = limits['memory']
        if not isinstance(memory, int) or memory < 128 or memory > 2048:
            raise ValidationError(
                'Memory must be between 128MB and 2048MB',
                field='limits.memory',
            )

    # Validate logs (MB)
    if 'logs' in limits:
        logs = limits['logs']
        if not isinstance(logs, int) or logs < 0 or logs > 10:
            raise ValidationError(
                'Logs must be between 0MB and 10MB',
                field='limits.logs',
            )


def validate_namespace_name(name: str) -> None:
    """Validate namespace name.

    Namespace names follow same rules as entity names.

    Args:
        name: Namespace name to validate.

    Raises:
        ValidationError: If name is invalid.
    """
    validate_entity_name(name, field='namespace')
