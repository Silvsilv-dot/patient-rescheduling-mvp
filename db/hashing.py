"""Hashing helpers for demo access codes.

Standard library only. The iteration count is a demo value; a real system
should use a modern password hash (for example argon2) with a higher cost.
Used by the seed script now and by ``api/security.py`` later.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from config.settings import PBKDF2_ITERATIONS

_ALGORITHM = "pbkdf2_sha256"


def hash_access_code(code: str, iterations: Optional[int] = None) -> str:
    """Return ``pbkdf2_sha256$iterations$salt_hex$hash_hex`` for a code."""
    rounds = iterations or PBKDF2_ITERATIONS
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, rounds)
    return f"{_ALGORITHM}${rounds}${salt.hex()}${digest.hex()}"


def verify_access_code(code: str, stored: str) -> bool:
    """Check a code against a stored hash in constant time."""
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", code.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)
