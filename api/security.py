"""Signed tokens and request rate limiting (BR-15, BR-20).

Tokens are short-lived (15 minutes), signed with a secret from the environment,
and carry only a role, a subject, and for patients the patient ID. Expiry is
checked against the ``now`` the caller supplies so tests can control time.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import jwt

from api.errors import ApiError
from config.settings import CONFIG_ERROR_MESSAGE, TOKEN_TTL_MIN, ConfigError, get_settings
from rules.business_rules import Role

ALGORITHM = "HS256"
MIN_KEY_LENGTH = 32
TOKEN_TTL_SECONDS = TOKEN_TTL_MIN * 60


@dataclass(frozen=True)
class TokenClaims:
    """What a verified token says about the caller."""

    role: str
    subject: str  # appointment ID for patients
    patient_id: Optional[str]
    issued_at: datetime
    expires_at: datetime


def _signing_key() -> str:
    key = get_settings().token_signing_key
    if not key or len(key) < MIN_KEY_LENGTH:
        raise ConfigError(CONFIG_ERROR_MESSAGE)  # generic: never says what is wrong
    return key


def create_token(role: str, subject: str, now: datetime, patient_id: Optional[str] = None) -> str:
    """Create a signed token that expires TOKEN_TTL_MIN minutes after ``now``."""
    payload: dict[str, object] = {
        "sub": subject,
        "role": Role(role).value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=TOKEN_TTL_MIN)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if patient_id:
        payload["pid"] = patient_id
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def decode_token(token: str, now: datetime) -> TokenClaims:
    """Verify signature and expiry. Any problem is a plain 401."""
    key = _signing_key()
    try:
        data = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],  # a token that claims 'none' or another algorithm is refused
            options={
                "verify_exp": False,  # checked below against the injected clock
                "verify_iat": False,
                "require": ["exp", "iat", "sub", "role"],
            },
        )
        expires_at = datetime.fromtimestamp(int(data["exp"]), tz=now.tzinfo)
        issued_at = datetime.fromtimestamp(int(data["iat"]), tz=now.tzinfo)
        role = Role(data["role"]).value
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise ApiError(401, "unauthorized") from None
    if now >= expires_at:
        raise ApiError(401, "unauthorized")
    return TokenClaims(
        role=role,
        subject=str(data["sub"]),
        patient_id=data.get("pid"),
        issued_at=issued_at,
        expires_at=expires_at,
    )


class RateLimiter:
    """Allow at most ``max_calls`` per ``window_seconds`` for each key (in memory, per process)."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, at: Optional[float] = None) -> bool:
        moment = time.monotonic() if at is None else at
        with self._lock:
            calls = self._calls[key]
            while calls and moment - calls[0] >= self.window:
                calls.popleft()
            if len(calls) >= self.max_calls:
                return False
            calls.append(moment)
            return True

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


# Sign-in is also protected by the per-appointment lockout (BR-20). This limit is a
# second, coarse guard per client address. It is per process, so a hosted copy with
# several instances would need a shared store.
auth_rate_limiter = RateLimiter(max_calls=60, window_seconds=60)
