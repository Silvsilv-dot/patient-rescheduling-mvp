"""Patient sign-in (BR-20). Staff sign-in is added in a later batch.

One generic failure result is used for unknown IDs and wrong codes, and both
paths do the same amount of hashing work so response time does not reveal
which appointment IDs exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

from db.hashing import hash_access_code, verify_access_code
from rules.business_rules import (
    AuditAction,
    Role,
    is_locked,
    register_failed_attempt,
)
from services import audit_service


@dataclass(frozen=True)
class AuthResult:
    """Outcome of a sign-in attempt."""

    ok: bool
    locked: bool = False
    appt_id: Optional[str] = None
    patient_id: Optional[str] = None


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A throwaway hash checked when the appointment ID is unknown (timing cover)."""
    return hash_access_code("000000")


def authenticate_patient(conn: Any, appt_id: str, code: str, now: datetime) -> AuthResult:
    """Check an appointment ID and access code, applying the lockout rule.

    Call inside ``get_conn()`` and read the result AFTER the block ends, so a
    failed attempt (which updates the counter) is committed.
    """
    row = conn.execute(
        "SELECT pa.access_code_hash, pa.failed_attempts, pa.locked_until, a.patient_id "
        "FROM patient_access pa JOIN appointments a ON a.appt_id = pa.appt_id "
        "WHERE pa.appt_id = %s FOR UPDATE OF pa",
        (appt_id,),
    ).fetchone()

    if row is None:
        verify_access_code(code, _decoy_hash())  # same work as a real check
        return AuthResult(ok=False)

    if is_locked(row["locked_until"], now):
        return AuthResult(ok=False, locked=True)

    if verify_access_code(code, row["access_code_hash"]):
        if row["failed_attempts"]:
            conn.execute(
                "UPDATE patient_access SET failed_attempts = 0, locked_until = NULL "
                "WHERE appt_id = %s",
                (appt_id,),
            )
        return AuthResult(ok=True, appt_id=appt_id, patient_id=row["patient_id"])

    attempts, locked_until = register_failed_attempt(row["failed_attempts"], now)
    conn.execute(
        "UPDATE patient_access SET failed_attempts = %s, locked_until = %s WHERE appt_id = %s",
        (attempts, locked_until, appt_id),
    )
    audit_service.record(conn, Role.PATIENT.value, AuditAction.SIGN_IN_FAILED.value, appt_id, now)
    if locked_until is not None:
        audit_service.record(
            conn, Role.PATIENT.value, AuditAction.SIGN_IN_LOCKED.value, appt_id, now
        )
    return AuthResult(ok=False)
