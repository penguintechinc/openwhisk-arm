"""Namespace model for OpenWhisk namespace management.

This module defines the Namespace model representing an isolated workspace
for OpenWhisk actions, triggers, rules, and packages. Each namespace has
its own quota limits and belongs to a single owner.

OpenWhisk namespace semantics:
- Namespaces provide multi-tenancy isolation
- Each namespace has a unique name and UUID
- UUID is used for API key binding (subject in OpenWhisk)
- Owner has full control over namespace resources
- Namespaces can have associated quota limits
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydal import DAL, Field


def define_namespace_table(db: DAL) -> None:
    """Define the namespace table in PyDAL.

    Args:
        db: PyDAL DAL instance.

    Table schema:
        - id: Auto-increment primary key
        - name: Unique namespace name (max 256 chars)
        - uuid: Unique UUID for API key binding
        - owner_id: Foreign key to auth_user table
        - description: Text description of namespace purpose
        - created_at: Timestamp of creation
        - updated_at: Timestamp of last update

    Indexes:
        - name: Unique index for fast lookups
        - uuid: Unique index for API key binding
        - owner_id: Index for owner-based queries
    """
    db.define_table(
        "namespace",
        Field(
            "name",
            "string",
            length=256,
            required=True,
            unique=True,
            notnull=True,
            label="Namespace Name",
            comment="Unique namespace identifier (e.g., 'user@example.com' or 'org/team')",
        ),
        Field(
            "uuid",
            "string",
            length=36,
            required=True,
            unique=True,
            notnull=True,
            label="Namespace UUID",
            comment="Unique UUID used for API key binding and authentication",
        ),
        Field(
            "owner_id",
            "reference auth_user",
            required=True,
            notnull=True,
            label="Owner",
            comment="User who owns this namespace",
        ),
        Field(
            "description",
            "text",
            required=False,
            label="Description",
            comment="Optional description of namespace purpose and contents",
        ),
        Field(
            "created_at",
            "datetime",
            default=lambda: datetime.utcnow(),
            required=True,
            notnull=True,
            label="Created At",
            comment="Timestamp when namespace was created",
        ),
        Field(
            "updated_at",
            "datetime",
            update=lambda: datetime.utcnow(),
            default=lambda: datetime.utcnow(),
            required=True,
            notnull=True,
            label="Updated At",
            comment="Timestamp when namespace was last updated",
        ),
        format="%(name)s",
    )

    # Create indexes for performance
    db.executesql(
        "CREATE INDEX IF NOT EXISTS idx_namespace_name ON namespace(name);"
    )
    db.executesql(
        "CREATE INDEX IF NOT EXISTS idx_namespace_uuid ON namespace(uuid);"
    )
    db.executesql(
        "CREATE INDEX IF NOT EXISTS idx_namespace_owner_id ON namespace(owner_id);"
    )

    # Set validation
    db.namespace.name.requires = [
        db.namespace.name._table._db.IS_NOT_EMPTY(),
        db.namespace.name._table._db.IS_LENGTH(maxsize=256),
        db.namespace.name._table._db.IS_NOT_IN_DB(db, "namespace.name"),
    ]

    db.namespace.uuid.requires = [
        db.namespace.uuid._table._db.IS_NOT_EMPTY(),
        db.namespace.uuid._table._db.IS_LENGTH(36),
        db.namespace.uuid._table._db.IS_NOT_IN_DB(db, "namespace.uuid"),
    ]

    db.namespace.owner_id.requires = db.namespace.owner_id._table._db.IS_IN_DB(
        db, "auth_user.id", "%(id)s"
    )
