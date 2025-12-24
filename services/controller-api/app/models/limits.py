"""Namespace limits model for OpenWhisk quota management.

This module defines the NamespaceLimits model that enforces quota limits
on namespace resources and operations. These limits control concurrency,
invocation rates, memory, timeouts, logs, and parameter sizes.

OpenWhisk quota semantics:
- Each namespace has ONE set of limits (1:1 relationship)
- Limits enforce multi-tenancy resource isolation
- concurrent_invocations: Max simultaneous action invocations
- invocations_per_minute: Rate limit for action invocations
- max_action_memory: Maximum memory allocation per action (MB)
- max_action_timeout: Maximum execution time per action (ms)
- max_action_logs: Maximum log size per activation (MB)
- max_parameters_size: Maximum size of action parameters (bytes)

Default limits:
- concurrent_invocations: 100 (simultaneous invocations)
- invocations_per_minute: 1000 (rate limiting)
- max_action_memory: 512 MB (per action instance)
- max_action_timeout: 60000 ms (1 minute)
- max_action_logs: 10 MB (per activation)
- max_parameters_size: 1048576 bytes (1 MB)
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydal import DAL, Field


def define_namespace_limits_table(db: DAL) -> None:
    """Define the namespace_limits table in PyDAL.

    Args:
        db: PyDAL DAL instance.

    Table schema:
        - id: Auto-increment primary key
        - namespace_id: Foreign key to namespace (unique, one limit per namespace)
        - concurrent_invocations: Max simultaneous invocations (default: 100)
        - invocations_per_minute: Max invocations per minute (default: 1000)
        - max_action_memory: Max memory in MB (default: 512)
        - max_action_timeout: Max timeout in ms (default: 60000)
        - max_action_logs: Max log size in MB (default: 10)
        - max_parameters_size: Max parameter size in bytes (default: 1048576)
        - created_at: Timestamp of creation
        - updated_at: Timestamp of last update

    Indexes:
        - namespace_id: Unique index ensuring one limit per namespace
    """
    db.define_table(
        "namespace_limits",
        Field(
            "namespace_id",
            "reference namespace",
            required=True,
            unique=True,
            notnull=True,
            label="Namespace",
            comment="Namespace these limits apply to (one limit set per namespace)",
        ),
        Field(
            "concurrent_invocations",
            "integer",
            default=100,
            required=True,
            notnull=True,
            label="Concurrent Invocations",
            comment="Maximum number of simultaneous action invocations (default: 100)",
        ),
        Field(
            "invocations_per_minute",
            "integer",
            default=1000,
            required=True,
            notnull=True,
            label="Invocations Per Minute",
            comment="Maximum action invocations per minute for rate limiting (default: 1000)",
        ),
        Field(
            "max_action_memory",
            "integer",
            default=512,
            required=True,
            notnull=True,
            label="Max Action Memory (MB)",
            comment="Maximum memory allocation per action instance in MB (default: 512)",
        ),
        Field(
            "max_action_timeout",
            "integer",
            default=60000,
            required=True,
            notnull=True,
            label="Max Action Timeout (ms)",
            comment="Maximum execution time per action in milliseconds (default: 60000 = 1 minute)",
        ),
        Field(
            "max_action_logs",
            "integer",
            default=10,
            required=True,
            notnull=True,
            label="Max Action Logs (MB)",
            comment="Maximum log size per activation in MB (default: 10)",
        ),
        Field(
            "max_parameters_size",
            "integer",
            default=1048576,
            required=True,
            notnull=True,
            label="Max Parameters Size (bytes)",
            comment="Maximum size of action parameters in bytes (default: 1048576 = 1 MB)",
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.utcnow(),
            required=True,
            notnull=True,
            label="Created At",
            comment="Timestamp when limits were created",
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.utcnow(),
            default=lambda: datetime.utcnow(),
            required=True,
            notnull=True,
            label="Updated At",
            comment="Timestamp when limits were last updated",
        ),
        format="Limits for %(namespace_id)s",
    )

    # Create unique index for namespace_id (one limit per namespace)
    db.executesql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_namespace_limits_namespace_id "
        "ON namespace_limits(namespace_id);"
    )

    # Set validation
    db.namespace_limits.namespace_id.requires = (
        db.namespace_limits.namespace_id._table._db.IS_IN_DB(
            db, "namespace.id", "%(name)s"
        )
    )

    # Validate positive integers for all limit fields
    db.namespace_limits.concurrent_invocations.requires = (
        db.namespace_limits.concurrent_invocations._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer"
        )
    )

    db.namespace_limits.invocations_per_minute.requires = (
        db.namespace_limits.invocations_per_minute._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer"
        )
    )

    db.namespace_limits.max_action_memory.requires = (
        db.namespace_limits.max_action_memory._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer (MB)"
        )
    )

    db.namespace_limits.max_action_timeout.requires = (
        db.namespace_limits.max_action_timeout._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer (ms)"
        )
    )

    db.namespace_limits.max_action_logs.requires = (
        db.namespace_limits.max_action_logs._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer (MB)"
        )
    )

    db.namespace_limits.max_parameters_size.requires = (
        db.namespace_limits.max_parameters_size._table._db.IS_INT_IN_RANGE(
            1, None, error_message="Must be a positive integer (bytes)"
        )
    )
