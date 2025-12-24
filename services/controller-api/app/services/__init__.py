"""Services package for PenguinWhisk Controller API.

This package provides service layer abstractions for storage, messaging,
invocation, and scheduling operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from flask import g

if TYPE_CHECKING:
    from app.services.invocation import InvocationService
    from app.services.messaging import MessagingService
    from app.services.scheduler import SchedulerService
    from app.services.storage import StorageService


def get_storage() -> Optional[StorageService]:
    """Get storage service instance (request-scoped).

    Returns:
        StorageService instance or None if not initialized
    """
    from flask import current_app

    return current_app.extensions.get('storage')


def get_messaging() -> Optional[MessagingService]:
    """Get messaging service instance (request-scoped).

    Returns:
        MessagingService instance or None if not initialized
    """
    from flask import current_app

    return current_app.extensions.get('messaging')


def get_invocation() -> Optional[InvocationService]:
    """Get invocation service instance (request-scoped).

    Returns:
        InvocationService instance or None if not initialized
    """
    from flask import current_app

    return current_app.extensions.get('invocation')


def get_scheduler() -> Optional[SchedulerService]:
    """Get scheduler service instance (request-scoped).

    Returns:
        SchedulerService instance or None if not initialized
    """
    from flask import current_app

    return current_app.extensions.get('scheduler')


__all__ = [
    'get_storage',
    'get_messaging',
    'get_invocation',
    'get_scheduler',
    'StorageService',
    'MessagingService',
    'InvocationService',
    'SchedulerService',
]
