"""Application settings and business constants.

Secrets are read from environment variables (or a local, git-ignored ``.env``
file). Nothing in this module reads secrets at import time: call
``get_settings()`` when you need them. Error messages never echo secret values.

Business constants (BR-xx) live here so every rule and test uses one source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

# --------------------------------------------------------------------------
# Business constants (assumptions to confirm with the product owner)
# --------------------------------------------------------------------------
LOCK_HOURS = 24  # BR-07 / BR-11: no patient reschedule inside this window
MAX_RESCHEDULES = 2  # BR-08
DELAY_THRESHOLD_MIN = 15  # BR-13: delayed if more than this many minutes
TOKEN_TTL_MIN = 15  # BR-20 / session timeout
SLOT_WINDOW_DAYS = 14  # BR-04: slots shown for the next N days
MIN_SLOTS = 3  # BR-04: minimum available slots to show
LOCKOUT_ATTEMPTS = 5  # BR-20: failed sign-ins before lockout
LOCKOUT_MINUTES = 5  # BR-20
REFRESH_SECONDS = 10  # simulated real-time refresh interval
REMINDER_HOURS_BEFORE = (48, 24)  # BR-14: simulated reminders
UTIL_LOW = 0.60  # staffing signal: under-used below this ratio
UTIL_HIGH = 0.90  # staffing signal: over capacity above this ratio

OFFICE_TZ = "America/New_York"  # fake office time zone for the demo
NOTICE_VERSION = "1.0"  # current privacy notice version (BR-18)
PBKDF2_ITERATIONS = 100_000  # demo value for hashing demo access codes
DEMO_BANNER_TEXT = "DEMO - Synthetic Data. Educational prototype, not a clinical system."

# BR-22: headers every API response must carry. The patient UI is served from the
# same origin, so the policy can be strict. (The interactive API docs page needs a
# relaxed policy and is handled separately in the API layer.)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}
NO_STORE_CACHE_CONTROL = "no-store"  # BR-22: appointment data is never cached

# Generic message used whenever a configuration problem must be reported.
CONFIG_ERROR_MESSAGE = "The application is not configured correctly."


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from the environment."""

    database_url: str
    token_signing_key: Optional[str]
    demo_staff_pin_frontdesk: Optional[str]
    demo_staff_pin_manager: Optional[str]
    db_pool_min: int
    db_pool_max: int
    db_pool_timeout: float

    def require(self, name: str) -> str:
        """Return a required optional setting or raise a generic ConfigError."""
        value = getattr(self, name, None)
        if not value:
            raise ConfigError(CONFIG_ERROR_MESSAGE)
        return value


def _load_dotenv_if_available() -> None:
    """Load a local .env file when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional
        return
    load_dotenv(override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(CONFIG_ERROR_MESSAGE) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read and validate settings. Cached; call ``reset_settings_cache`` in tests."""
    _load_dotenv_if_available()

    database_url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if not database_url or not database_url.startswith(("postgresql://", "postgres://")):
        # Do not echo the value: it contains credentials.
        raise ConfigError(CONFIG_ERROR_MESSAGE)

    return Settings(
        database_url=database_url,
        token_signing_key=os.environ.get("TOKEN_SIGNING_KEY") or None,
        demo_staff_pin_frontdesk=os.environ.get("DEMO_STAFF_PIN_FRONTDESK") or None,
        demo_staff_pin_manager=os.environ.get("DEMO_STAFF_PIN_MANAGER") or None,
        db_pool_min=_int_env("DB_POOL_MIN", 1),
        db_pool_max=_int_env("DB_POOL_MAX", 5),
        db_pool_timeout=float(_int_env("DB_POOL_TIMEOUT_SECONDS", 5)),
    )


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests)."""
    get_settings.cache_clear()
