"""Serialization utilities for OpenWhisk entities.

Converts PyDAL Row objects to OpenWhisk-compatible JSON format.
"""

from __future__ import annotations

from typing import Any

from pydal.objects import Row


def serialize_namespace(row: Row) -> dict[str, Any]:
    """Serialize namespace to OpenWhisk format.

    Args:
        row: PyDAL Row object.

    Returns:
        Dictionary in OpenWhisk namespace format.
    """
    return {
        'name': row.name,
        'owner_id': row.owner_id,
        'description': row.description or '',
        'limits': row.limits or {},
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_package(row: Row, namespace_name: str | None = None) -> dict[str, Any]:
    """Serialize package to OpenWhisk format.

    Args:
        row: PyDAL Row object.
        namespace_name: Optional namespace name for fully qualified name.

    Returns:
        Dictionary in OpenWhisk package format.
    """
    result = {
        'name': row.name,
        'namespace': namespace_name or str(row.namespace_id),
        'version': row.version,
        'publish': row.publish,
        'annotations': row.annotations or [],
        'parameters': row.parameters or [],
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }

    # Add fully qualified name if namespace provided
    if namespace_name:
        result['namespace'] = namespace_name
        result['fqn'] = f'/{namespace_name}/{row.name}'

    return result


def serialize_action(
    row: Row,
    namespace_name: str | None = None,
    package_name: str | None = None,
) -> dict[str, Any]:
    """Serialize action to OpenWhisk format.

    Args:
        row: PyDAL Row object.
        namespace_name: Optional namespace name for fully qualified name.
        package_name: Optional package name for fully qualified name.

    Returns:
        Dictionary in OpenWhisk action format.
    """
    result = {
        'name': row.name,
        'namespace': namespace_name or str(row.namespace_id),
        'version': row.version,
        'publish': row.publish,
        'annotations': row.annotations or [],
        'parameters': row.parameters or [],
        'limits': row.limits or {
            'timeout': 60000,
            'memory': 256,
            'logs': 10,
        },
        'exec': {
            'kind': row.exec_kind,
            'code': row.exec_code,
            'binary': row.exec_binary,
        },
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }

    # Add exec.main if present
    if row.exec_main:
        result['exec']['main'] = row.exec_main

    # Build fully qualified name
    if namespace_name:
        result['namespace'] = namespace_name
        if package_name:
            result['fqn'] = f'/{namespace_name}/{package_name}/{row.name}'
        else:
            result['fqn'] = f'/{namespace_name}/{row.name}'

    return result


def serialize_trigger(row: Row, namespace_name: str | None = None) -> dict[str, Any]:
    """Serialize trigger to OpenWhisk format.

    Args:
        row: PyDAL Row object.
        namespace_name: Optional namespace name for fully qualified name.

    Returns:
        Dictionary in OpenWhisk trigger format.
    """
    result = {
        'name': row.name,
        'namespace': namespace_name or str(row.namespace_id),
        'version': row.version,
        'publish': row.publish,
        'annotations': row.annotations or [],
        'parameters': row.parameters or [],
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }

    # Add fully qualified name if namespace provided
    if namespace_name:
        result['namespace'] = namespace_name
        result['fqn'] = f'/{namespace_name}/{row.name}'

    return result


def serialize_rule(
    row: Row,
    namespace_name: str | None = None,
    trigger_name: str | None = None,
    action_name: str | None = None,
) -> dict[str, Any]:
    """Serialize rule to OpenWhisk format.

    Args:
        row: PyDAL Row object.
        namespace_name: Optional namespace name for fully qualified name.
        trigger_name: Optional trigger name.
        action_name: Optional action name.

    Returns:
        Dictionary in OpenWhisk rule format.
    """
    result = {
        'name': row.name,
        'namespace': namespace_name or str(row.namespace_id),
        'version': row.version,
        'status': row.status,
        'trigger': trigger_name or str(row.trigger_id),
        'action': action_name or str(row.action_id),
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }

    # Add fully qualified name if namespace provided
    if namespace_name:
        result['namespace'] = namespace_name
        result['fqn'] = f'/{namespace_name}/{row.name}'

    return result


def serialize_activation(
    row: Row,
    namespace_name: str | None = None,
    action_name: str | None = None,
) -> dict[str, Any]:
    """Serialize activation to OpenWhisk format.

    Args:
        row: PyDAL Row object.
        namespace_name: Optional namespace name.
        action_name: Optional action name.

    Returns:
        Dictionary in OpenWhisk activation format.
    """
    result = {
        'activationId': row.activation_id,
        'namespace': namespace_name or str(row.namespace_id),
        'name': action_name or str(row.action_id),
        'version': row.version or '0.0.1',
        'publish': row.publish,
        'annotations': row.annotations or [],
        'start': row.start,
        'end': row.end,
        'duration': row.duration,
        'statusCode': row.status_code,
        'response': row.response or {},
        'logs': row.logs or [],
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }

    # Add cause if present
    if row.cause:
        result['cause'] = row.cause

    return result


def build_fqn(namespace: str, package: str | None, name: str) -> str:
    """Build fully qualified name for OpenWhisk entity.

    Args:
        namespace: Namespace name.
        package: Optional package name.
        name: Entity name.

    Returns:
        Fully qualified name in format: /namespace/[package/]name
    """
    if package:
        return f'/{namespace}/{package}/{name}'
    return f'/{namespace}/{name}'


def parse_fqn(fqn: str) -> tuple[str, str | None, str]:
    """Parse fully qualified name into components.

    Args:
        fqn: Fully qualified name (e.g., '/namespace/action' or '/namespace/pkg/action').

    Returns:
        Tuple of (namespace, package, name) where package may be None.
    """
    parts = fqn.strip('/').split('/')

    if len(parts) == 2:
        return parts[0], None, parts[1]
    elif len(parts) == 3:
        return parts[0], parts[1], parts[2]
    else:
        raise ValueError(f'Invalid fully qualified name: {fqn}')
