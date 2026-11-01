"""Fixtures for the API tests that need a real database.

They run only when TEST_DATABASE_URL points at a TEST database (it gets wiped):
GitHub's CI supplies one automatically. Each test starts from freshly loaded
synthetic data, so tests cannot affect each other.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api import deps
from api.main import create_app
from api.security import auth_rate_limiter
from config import settings as cfg
from db import hashing
from db import seed_synthetic as seed
from db.connection import close_pool, get_conn
from services import auth_service
from tests.conftest import FIXED_NOW

TEST_KEY = "test-signing-key-" + "x" * 32


class Clock:
    """A clock tests can move: the API reads 'now' from here."""

    def __init__(self) -> None:
        self.now = FIXED_NOW


@pytest.fixture(scope="session")
def _schema_ready() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("NEON_DATABASE_URL", url)
        cfg.reset_settings_cache()
        close_pool()
        with get_conn() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {', '.join(reversed(seed.ALL_TABLES))} CASCADE")
            seed.apply_schema(conn)
        close_pool()
    cfg.reset_settings_cache()
    yield url


@pytest.fixture
def fresh_db(_schema_ready, monkeypatch, dataset):
    """Fresh synthetic data for one test (the shared fixed dataset)."""
    monkeypatch.setenv("NEON_DATABASE_URL", _schema_ready)
    monkeypatch.setenv("TOKEN_SIGNING_KEY", TEST_KEY)
    monkeypatch.setattr(hashing, "PBKDF2_ITERATIONS", 1000)  # fast hashing for tests
    auth_service._decoy_hash.cache_clear()
    cfg.reset_settings_cache()
    close_pool()
    with get_conn() as conn:
        seed.reset_tables(conn)
        seed.load_dataset(conn, dataset)
    auth_rate_limiter.reset()
    yield dataset
    close_pool()
    cfg.reset_settings_cache()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(fresh_db, clock):
    application = create_app(static_dir=None)
    application.dependency_overrides[deps.get_now] = lambda: clock.now
    return application


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    """Run queries directly against the test database (to check what really happened)."""

    def run(sql: str, params: tuple = ()) -> list[dict]:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    return run
