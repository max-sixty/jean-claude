"""Pytest configuration and fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.imessage_db import DatabaseBuilder, create_sample_database
from tests.fixtures.whatsapp_db import (
    DatabaseBuilder as WhatsAppDatabaseBuilder,
    create_sample_database as create_whatsapp_sample_database,
)


@pytest.fixture
def imessage_db_builder() -> DatabaseBuilder:
    """Provide a fresh DatabaseBuilder for custom test scenarios."""
    return DatabaseBuilder()


@pytest.fixture
def imessage_sample_db():
    """Provide an in-memory database with sample data.

    Returns the connection directly for query testing.
    Closes the connection after the test completes.
    """
    builder = create_sample_database()
    conn = builder.build()
    yield conn
    conn.close()


@pytest.fixture
def imessage_sample_db_path(tmp_path: Path) -> Path:
    """Provide a file-based database with sample data.

    Returns the path to the database file. Useful for testing
    code that needs a file path rather than a connection.
    """
    db_path = tmp_path / "chat.db"
    builder = create_sample_database()
    conn = builder.build(db_path)
    conn.close()
    return db_path


# =============================================================================
# WhatsApp Fixtures
# =============================================================================


@pytest.fixture
def whatsapp_db_builder() -> WhatsAppDatabaseBuilder:
    """Provide a fresh WhatsApp DatabaseBuilder for custom test scenarios."""
    return WhatsAppDatabaseBuilder()


@pytest.fixture
def whatsapp_data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory with sample WhatsApp database.

    Returns the data directory path for use with WHATSAPP_DATA_DIR env var.
    The directory contains messages.db with sample data.
    """
    data_dir = tmp_path / "whatsapp"
    data_dir.mkdir()

    db_path = data_dir / "messages.db"
    builder = create_whatsapp_sample_database()
    conn = builder.build(db_path)
    conn.close()

    return data_dir
