"""Neon Postgres connection pool.

* One lazily created, thread-safe pool per process.
* Connections are checked before use, which handles Neon closing idle
  connections after scale-to-zero.
* Errors are converted to ``DatabaseUnavailableError`` with a generic message;
  connection strings and server messages are never exposed.

Usage::

    from db.connection import get_conn

    with get_conn() as conn:          # commits on success, rolls back on error
        rows = conn.execute("SELECT 1 AS one").fetchall()
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from psycopg import Connection, OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from config.settings import ConfigError, get_settings

logger = logging.getLogger(__name__)

GENERIC_DB_MESSAGE = "The service is temporarily unavailable. Please try again shortly."

_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


class DatabaseUnavailableError(RuntimeError):
    """Raised when the database cannot be reached. Message is always generic."""


def get_pool() -> ConnectionPool:
    """Return the shared pool, creating it on first use."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                settings = get_settings()  # may raise ConfigError (generic message)
                pool = ConnectionPool(
                    conninfo=settings.database_url,
                    min_size=settings.db_pool_min,
                    max_size=settings.db_pool_max,
                    timeout=settings.db_pool_timeout,
                    # prepare_threshold=None turns off server-side prepared statements,
                    # which are not safe behind Neon's pooled (PgBouncer) endpoint.
                    kwargs={"row_factory": dict_row, "prepare_threshold": None},
                    check=ConnectionPool.check_connection,
                    open=False,
                    name="appointments-pool",
                )
                pool.open(wait=False)
                _pool = pool
    return _pool


@contextmanager
def get_conn() -> Iterator[Connection[Any]]:
    """Yield a pooled connection inside a transaction.

    The transaction commits when the block ends normally and rolls back if an
    exception escapes. Rows are returned as dictionaries.
    """
    pool = get_pool()
    try:
        with pool.connection() as conn:
            yield conn
    except (PoolTimeout, OperationalError) as exc:
        # Log the class only: the message may contain host names.
        logger.warning("Database error: %s", type(exc).__name__)
        raise DatabaseUnavailableError(GENERIC_DB_MESSAGE) from None


def health_check() -> dict[str, str]:
    """Return service health without ever raising or leaking details."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "ok"}
    except (DatabaseUnavailableError, ConfigError):
        return {"status": "degraded", "database": "unavailable"}


def warm_up() -> bool:
    """Run a trivial query so the first user request is fast (Neon cold start)."""
    return health_check()["database"] == "ok"


def close_pool() -> None:
    """Close the pool (used at shutdown and in tests)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
