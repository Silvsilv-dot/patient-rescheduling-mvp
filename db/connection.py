"""Pooled Neon PostgreSQL connection helper tuned for Vercel Serverless."""
from __future__ import annotations

import os
from contextlib import contextmanager
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.getenv("NEON_DATABASE_URL", "").strip()
        if not dsn:
            raise RuntimeError("NEON_DATABASE_URL is not configured.")
        # Keep max_size small (2) for serverless PgBouncer compatibility
        _pool = ConnectionPool(
            conninfo=dsn,
            min_size=1,
            max_size=2,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
    return _pool


@contextmanager
def get_db_cursor():
    pool = get_pool()
    with pool.connection() as conn:
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
