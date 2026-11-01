"""Rescheduling (BR-06 to BR-10, BR-25).

Everything happens in the caller's single database transaction: if any step
fails, nothing changes. The double-booking guard is the conditional UPDATE that
claims the new slot (plus the unique index in schema.sql).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from rules.business_rules import (
    AuditAction,
    Role,
    RuleResult,
    SlotInfo,
    idempotency_outcome,
    plan_reschedule,
    request_fingerprint,
)
from services import appointment_service, audit_service, consent_service, reminder_service
from services.exceptions import ConflictError, RuleDenied
from services.slot_service import other_active_ranges

_SLOT_SQL = """
SELECT slot_id, provider_id, type_id, start_time, end_time, status
FROM slots WHERE slot_id = %s
"""


def reschedule(
    conn: Any,
    appt_id: str,
    new_slot_id: str,
    idempotency_key: str,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    """Move an appointment to a new slot. Returns (result, was_replay)."""
    # Locking the appointment row makes requests for the same appointment run one at a time.
    record = appointment_service.get_record(conn, appt_id, lock=True)

    endpoint = f"reschedule:{appt_id}"
    fingerprint = request_fingerprint(endpoint, {"new_slot_id": new_slot_id})
    stored = conn.execute(
        "SELECT request_hash, response_body FROM idempotency_keys "
        "WHERE idem_key = %s AND endpoint = %s AND subject_id = %s",
        (idempotency_key, endpoint, record.patient_id),
    ).fetchone()
    outcome = idempotency_outcome(stored["request_hash"] if stored else None, fingerprint)
    if outcome == "replay":
        return stored["response_body"], True
    if outcome == "conflict":
        raise ConflictError("idempotency_conflict")

    slot_row = conn.execute(_SLOT_SQL, (new_slot_id,)).fetchone()
    if slot_row is None:
        raise RuleDenied(RuleResult.deny("slot_unavailable"))
    new_slot = SlotInfo(**slot_row)

    result, plan = plan_reschedule(record.info(), new_slot, now, other_active_ranges(conn, record))
    if plan is None:
        raise RuleDenied(result)

    # BR-10: claim the new slot atomically. Only one of several simultaneous
    # requests can change it from Available to Booked; the others get zero rows.
    claimed = conn.execute(
        "UPDATE slots SET status = 'Booked' "
        "WHERE slot_id = %s AND status = 'Available' AND start_time > %s AND type_id = %s "
        "RETURNING slot_id",
        (plan.new_slot_id, now, record.type_id),
    ).fetchone()
    if claimed is None:
        raise RuleDenied(RuleResult.deny("slot_unavailable"))

    conn.execute(
        "UPDATE slots SET status = %s WHERE slot_id = %s",
        (plan.old_slot_status, plan.old_slot_id),
    )
    try:
        conn.execute(
            "UPDATE appointments SET slot_id = %s, status = %s, reschedule_count = %s, "
            "updated_at = %s WHERE appt_id = %s",
            (plan.new_slot_id, plan.new_status, plan.new_reschedule_count, now, appt_id),
        )
    except psycopg.errors.UniqueViolation:
        raise RuleDenied(RuleResult.deny("slot_unavailable")) from None

    conn.execute(
        "INSERT INTO reschedule_history (appt_id, old_slot_id, new_slot_id, old_start, "
        "new_start, changed_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (appt_id, plan.old_slot_id, plan.new_slot_id, plan.old_start, plan.new_start, now),
    )
    reminder_service.regenerate(
        conn, appt_id, plan.new_start, consent_service.is_c2_granted(conn, record.patient_id)
    )
    audit_service.record(
        conn, Role.PATIENT.value, AuditAction.APPOINTMENT_RESCHEDULE.value, appt_id, now
    )

    updated = appointment_service.get_record(conn, appt_id)
    body = {
        "appt_id": appt_id,
        "status": updated.status,
        "old_start_time": plan.old_start.isoformat(),
        "new_start_time": plan.new_start.isoformat(),
        "reschedule_count": updated.reschedule_count,
        "provider": updated.provider_name,
        "location": updated.location_name,
        "label": updated.type_label,
    }
    conn.execute(
        "INSERT INTO idempotency_keys (idem_key, endpoint, subject_id, request_hash, "
        "status_code, response_body, created_at) VALUES (%s, %s, %s, %s, 200, %s, %s)",
        (idempotency_key, endpoint, record.patient_id, fingerprint, Jsonb(body), now),
    )
    return body, False
