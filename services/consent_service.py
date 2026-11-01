"""Consent records (BR-18, BR-19, BR-24): privacy notice C1 and reminder choice C2."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from config.settings import NOTICE_VERSION
from rules.business_rules import (
    AuditAction,
    ConsentStatus,
    ConsentType,
    Role,
    c1_satisfied,
    reminders_enabled,
)
from services import audit_service
from services.exceptions import ConflictError

_AUDIT_FOR_STATUS = {
    ConsentStatus.GRANTED.value: AuditAction.CONSENT_GRANT.value,
    ConsentStatus.DECLINED.value: AuditAction.CONSENT_DECLINE.value,
    ConsentStatus.WITHDRAWN.value: AuditAction.CONSENT_WITHDRAW.value,
}


def get_notice(conn: Any) -> Optional[dict[str, Any]]:
    """The current privacy notice, or None if it has not been loaded."""
    return conn.execute(
        "SELECT version, notice_text, effective_from FROM notice_versions WHERE version = %s",
        (NOTICE_VERSION,),
    ).fetchone()


def latest_consent(conn: Any, subject_id: str, consent_type: str) -> Optional[dict[str, Any]]:
    """The most recent consent record of one type for one person."""
    return conn.execute(
        "SELECT status, notice_version, created_at FROM consents "
        "WHERE subject_id = %s AND consent_type = %s "
        "ORDER BY created_at DESC, consent_id DESC LIMIT 1",
        (subject_id, consent_type),
    ).fetchone()


def is_c1_satisfied(conn: Any, patient_id: str) -> bool:
    """BR-18: C1 granted for the current notice version."""
    latest = latest_consent(conn, patient_id, ConsentType.NOTICE.value)
    return c1_satisfied(
        latest["status"] if latest else None,
        latest["notice_version"] if latest else None,
        NOTICE_VERSION,
    )


def is_c2_granted(conn: Any, patient_id: str) -> bool:
    """BR-19: reminders are on only while the latest C2 record is Granted."""
    latest = latest_consent(conn, patient_id, ConsentType.REMINDERS.value)
    return reminders_enabled(latest["status"] if latest else None)


def record_consent(
    conn: Any,
    *,
    patient_id: str,
    consent_type: str,
    status: str,
    notice_version: str,
    now: datetime,
) -> dict[str, Any]:
    """Store a consent choice, write the audit entry, and keep reminders in sync."""
    if (
        consent_type == ConsentType.NOTICE.value
        and status == ConsentStatus.GRANTED.value
        and notice_version != NOTICE_VERSION
    ):
        raise ConflictError("notice_outdated")

    conn.execute(
        "INSERT INTO consents (subject_id, subject_role, consent_type, notice_version, "
        "status, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (patient_id, Role.PATIENT.value, consent_type, notice_version, status, now),
    )
    audit_service.record(conn, Role.PATIENT.value, _AUDIT_FOR_STATUS[status], None, now)

    if consent_type == ConsentType.REMINDERS.value:
        # Withdrawal takes effect immediately everywhere (BR-19).
        conn.execute(
            "UPDATE reminders SET suppressed = %s WHERE appt_id IN "
            "(SELECT appt_id FROM appointments WHERE patient_id = %s)",
            (status != ConsentStatus.GRANTED.value, patient_id),
        )
    return {
        "consent_type": consent_type,
        "status": status,
        "notice_version": notice_version,
        "recorded_at": now,
    }


def summary(conn: Any, patient_id: str) -> dict[str, Any]:
    """Current consent state for the patient page."""
    c1 = latest_consent(conn, patient_id, ConsentType.NOTICE.value)
    c2 = latest_consent(conn, patient_id, ConsentType.REMINDERS.value)
    return {
        "notice_version_current": NOTICE_VERSION,
        "c1": {
            "status": c1["status"] if c1 else None,
            "notice_version": c1["notice_version"] if c1 else None,
            "satisfied": is_c1_satisfied(conn, patient_id),
        },
        "c2": {
            "status": c2["status"] if c2 else None,
            "notice_version": c2["notice_version"] if c2 else None,
            "satisfied": is_c2_granted(conn, patient_id),
        },
    }
