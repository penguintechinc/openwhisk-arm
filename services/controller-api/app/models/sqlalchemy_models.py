"""SQLAlchemy models for Flask-Security-Too authentication.

This module defines User and Role models using SQLAlchemy for Flask-Security-Too
integration. These models handle authentication and authorization.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from flask_security import RoleMixin, UserMixin
from flask_sqlalchemy import SQLAlchemy

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped

# SQLAlchemy database instance
db = SQLAlchemy()


# Association table for many-to-many relationship between users and roles
roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True),
)


class Role(db.Model, RoleMixin):
    """Role model for Flask-Security-Too RBAC."""

    __tablename__ = 'role'

    id: Mapped[int] = db.Column(db.Integer, primary_key=True)
    name: Mapped[str] = db.Column(db.String(80), unique=True, nullable=False)
    description: Mapped[str] = db.Column(db.String(255), nullable=True)
    created_at: Mapped[datetime] = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        """String representation of Role."""
        return f'<Role {self.name}>'


class User(db.Model, UserMixin):
    """User model for Flask-Security-Too authentication."""

    __tablename__ = 'user'

    id: Mapped[int] = db.Column(db.Integer, primary_key=True)
    email: Mapped[str] = db.Column(db.String(255), unique=True, nullable=False)
    username: Mapped[str] = db.Column(db.String(255), unique=True, nullable=True)
    password: Mapped[str] = db.Column(db.String(255), nullable=False)
    active: Mapped[bool] = db.Column(db.Boolean, nullable=False, default=True)
    confirmed_at: Mapped[datetime] = db.Column(db.DateTime, nullable=True)
    fs_uniquifier: Mapped[str] = db.Column(
        db.String(64), unique=True, nullable=False
    )

    # Additional user fields
    first_name: Mapped[str] = db.Column(db.String(100), nullable=True)
    last_name: Mapped[str] = db.Column(db.String(100), nullable=True)
    created_at: Mapped[datetime] = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Login tracking
    last_login_at: Mapped[datetime] = db.Column(db.DateTime, nullable=True)
    current_login_at: Mapped[datetime] = db.Column(db.DateTime, nullable=True)
    last_login_ip: Mapped[str] = db.Column(db.String(100), nullable=True)
    current_login_ip: Mapped[str] = db.Column(db.String(100), nullable=True)
    login_count: Mapped[int] = db.Column(db.Integer, nullable=False, default=0)

    # API key field (legacy, single key per user)
    api_key: Mapped[str] = db.Column(db.String(64), nullable=True)

    # Relationships
    roles = db.relationship(
        'Role',
        secondary=roles_users,
        backref=db.backref('users', lazy='dynamic'),
    )

    def __repr__(self) -> str:
        """String representation of User."""
        return f'<User {self.email}>'

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role.

        Args:
            role_name: Name of role to check.

        Returns:
            True if user has role, False otherwise.
        """
        return any(role.name == role_name for role in self.roles)
