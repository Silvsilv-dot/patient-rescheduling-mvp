"""Fixtures for the browser tests: a real server on a freshly seeded test database."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from config import settings as cfg
from db import hashing
from db import seed_synthetic as seed
from db.connection import close_pool, get_conn
from tests.ui.stack import SIGNING_KEY, describe, office_now, running_server


@pytest.fixture(scope="session")
def ui_database() -> Iterator[str]:
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
def ui_stack(ui_database, monkeypatch) -> Iterator[dict]:
    """Fresh data (dated from today, like the real demo), plus a running server."""
    monkeypatch.setenv("NEON_DATABASE_URL", ui_database)
    monkeypatch.setenv("TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setattr(hashing, "PBKDF2_ITERATIONS", 1000)  # fast hashing for tests
    cfg.reset_settings_cache()
    close_pool()
    dataset = seed.generate_dataset(now=seed.default_seed_now())
    with get_conn() as conn:
        seed.reset_tables(conn)
        seed.load_dataset(conn, dataset)
    close_pool()
    info = describe(dataset, office_now())
    with running_server(ui_database) as base:
        yield {"base": base, "creds": {"far": info["far"], "soon": info["soon"]}}
    cfg.reset_settings_cache()
