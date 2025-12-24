"""Pytest configuration and fixtures for PenguinWhisk Controller API tests."""

from __future__ import annotations

from typing import Generator

import pytest
from flask import Flask

from app import create_app
from app.config import TestingConfig


@pytest.fixture
def app() -> Flask:
    """Create and configure a test Flask application.

    Returns:
        Flask application configured for testing.
    """
    app = create_app(TestingConfig)
    return app


@pytest.fixture
def client(app: Flask):
    """Create a test client for the Flask application.

    Args:
        app: Flask application fixture.

    Returns:
        Flask test client.
    """
    return app.test_client()


@pytest.fixture
def runner(app: Flask):
    """Create a test CLI runner for the Flask application.

    Args:
        app: Flask application fixture.

    Returns:
        Flask CLI test runner.
    """
    return app.test_cli_runner()


@pytest.fixture
def app_context(app: Flask) -> Generator:
    """Provide an application context for tests.

    Args:
        app: Flask application fixture.

    Yields:
        Application context.
    """
    with app.app_context():
        yield app


@pytest.fixture
def request_context(app: Flask) -> Generator:
    """Provide a request context for tests.

    Args:
        app: Flask application fixture.

    Yields:
        Request context.
    """
    with app.test_request_context():
        yield app
