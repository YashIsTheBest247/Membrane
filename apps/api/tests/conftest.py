"""Test fixtures.

Each test module gets a fresh SQLite database and a fresh in-memory taint
graph, so no test can pass because of state another test left behind.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

# Configure before anything imports the settings singleton.
_DB_PATH = Path(__file__).parent / f"test_{uuid.uuid4().hex[:8]}.db"
os.environ.setdefault("MEMBRANE_DATABASE_URL",
                      f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}")
os.environ.setdefault("MEMBRANE_SIGNING_KEY", "test-signing-key")
os.environ.setdefault("MEMBRANE_HASH_SALT", "test-hash-salt")
os.environ.setdefault("MEMBRANE_APPROVAL_TIMEOUT_SECONDS", "2")
os.environ.setdefault("MEMBRANE_BREAKER_HOLD_THRESHOLD", "5")

from fastapi.testclient import TestClient  # noqa: E402

from membrane.circuit import breaker  # noqa: E402
from membrane.layers.l3_taint import tracker  # noqa: E402
from membrane.main import create_app  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run an async test")


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_id():
    """A unique session per test, so provenance never leaks between them."""
    return f"test_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _clean_in_memory_state():
    yield
    tracker._graphs.clear()
    breaker._states.clear()


@pytest.fixture
async def app_context():
    """Database only, for tests that do not need the HTTP surface."""
    from membrane.db import init_db

    await init_db()
    yield


def pytest_sessionfinish(session, exitstatus):
    try:
        if _DB_PATH.exists():
            _DB_PATH.unlink()
    except OSError:
        pass
