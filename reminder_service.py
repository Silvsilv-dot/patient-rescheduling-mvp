"""Simulated reminders (BR-14, BR-19). Nothing is ever sent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rules.business_rules import reminder_due_times
from services import consent_service

# Kept generic on purpose: no provider, place or appointment type.
REMINDER_MESSAGE = (
    "Reminder: you have an upcoming appointment. This is a simulated message and is never sent."
)


def list_reminders(conn: Any, appt_id: str, patient_id: str) -> dict[str, Any]:
    """Reminders for display. Empty unless the patient has opted in (BR-19)."""
    if not consent_service.is_c2_granted(conn, patient_id):
        return {"reminders_enabled": False, "reminders": []}
    rows = conn.execute(
        "SELECT due_at, status FROM reminders WHERE appt_id = %s ORDER BY due_at",
        (appt_id,),
    ).fetchall()
    return {
        "reminders_enabled": True,
        "reminders": [
            {"due_at": r["due_at"], "message": REMINDER_MESSAGE, "status": r["status"]}
            for r in rows
        ],
    }


def regenerate(conn: Any, appt_id: str, start_time: datetime, enabled: bool) -> None:
    """Replace an appointment's reminders after its time changes (48 h and 24 h before)."""
    delete_for_appointment(conn, appt_id)
    rows = [(appt_id, due, not enabled) for due in reminder_due_times(start_time)]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO reminders (appt_id, due_at, status, suppressed) "
            "VALUES (%s, %s, 'Simulated', %s)",
            rows,
        )


def delete_for_appointment(conn: Any, appt_id: str) -> None:
    """Remove reminders (used when an appointment is cancelled or rescheduled)."""
    conn.execute("DELETE FROM reminders WHERE appt_id = %s", (appt_id,))
