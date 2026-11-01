"""Reading, confirming and cancelling an appointment (BR-02, BR-03, BR-11)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import OFFICE_TZ
from rules.business_rules import (
    AppointmentInfo,
    AppointmentStatus,
    AuditAction,
    Role,
    SlotStatus,
    can_confirm,
    patient_actions,
    plan_cancel,
    restrict_to_allowed,
)
from services import audit_service, reminder_service
from services.exceptions import NotFoundError, RuleDenied

_RECORD_SQL = """
SELECT a.appt_id, a.patient_id, a.status, a.type_id, a.slot_id, a.reschedule_count,
       s.start_time, s.end_time, s.provider_id,
       p.display_name AS provider_name, l.name AS location_name, t.label AS type_label
FROM appointments a
JOIN slots s ON s.slot_id = a.slot_id
JOIN providers p ON p.provider_id = s.provider_id
JOIN locations l ON l.location_id = s.location_id
JOIN appointment_types t ON t.type_id = a.type_id
WHERE a.appt_id = %s
"""


@dataclass(frozen=True)
class AppointmentRecord:
    """An appointment joined with its slot, provider, location and type."""

    appt_id: str
    patient_id: str
    status: str
    type_id: str
    type_label: str
    slot_id: str
    start_time: datetime
    end_time: datetime
    provider_id: str
    provider_name: str
    location_name: str
    reschedule_count: int

    def info(self) -> AppointmentInfo:
        """The subset the pure rules need."""
        return AppointmentInfo(
            self.appt_id,
            self.status,
            self.type_id,
            self.slot_id,
            self.start_time,
            self.reschedule_count,
        )


def get_record(conn: Any, appt_id: str, lock: bool = False) -> AppointmentRecord:
    """Load an appointment. ``lock=True`` locks its row until the transaction ends."""
    sql = _RECORD_SQL + (" FOR UPDATE OF a" if lock else "")
    row: Optional[dict[str, Any]] = conn.execute(sql, (appt_id,)).fetchone()
    if row is None:
        raise NotFoundError(appt_id)
    return AppointmentRecord(**row)


def appointment_view(record: AppointmentRecord, now: datetime) -> dict[str, Any]:
    """The minimum-necessary patient view (BR-03, BR-21)."""
    office_tz = ZoneInfo(OFFICE_TZ)
    actions = patient_actions(record.status, record.start_time, record.reschedule_count, now)
    view = {
        "appt_id": record.appt_id,
        "start_time": record.start_time.astimezone(office_tz),
        "time_zone": OFFICE_TZ,
        "location": record.location_name,
        "label": record.type_label,
        "status": record.status,
        "allowed_actions": {
            name: {"allowed": result.ok, "message": result.message}
            for name, result in actions.items()
        },
    }
    return restrict_to_allowed("patient_appointment", view)


def confirm(conn: Any, appt_id: str, now: datetime) -> AppointmentRecord:
    """BR-02: confirm an active appointment."""
    record = get_record(conn, appt_id, lock=True)
    result = can_confirm(record.status)
    if not result.ok:
        raise RuleDenied(result)
    conn.execute(
        "UPDATE appointments SET status = %s, updated_at = %s WHERE appt_id = %s",
        (AppointmentStatus.CONFIRMED.value, now, appt_id),
    )
    audit_service.record(
        conn, Role.PATIENT.value, AuditAction.APPOINTMENT_CONFIRM.value, appt_id, now
    )
    return get_record(conn, appt_id)


def cancel(conn: Any, appt_id: str, now: datetime) -> AppointmentRecord:
    """BR-11: cancel, release the slot immediately, and flag a late cancellation."""
    record = get_record(conn, appt_id, lock=True)
    result, plan = plan_cancel(record.info(), now)
    if plan is None:
        raise RuleDenied(result)
    conn.execute(
        "UPDATE appointments SET status = %s, late_cancellation = %s, updated_at = %s "
        "WHERE appt_id = %s",
        (plan.new_status, plan.late_cancellation, now, appt_id),
    )
    conn.execute(
        "UPDATE slots SET status = %s WHERE slot_id = %s",
        (SlotStatus.AVAILABLE.value, plan.slot_id),
    )
    reminder_service.delete_for_appointment(conn, appt_id)
    audit_service.record(
        conn, Role.PATIENT.value, AuditAction.APPOINTMENT_CANCEL.value, appt_id, now
    )
    return get_record(conn, appt_id)
