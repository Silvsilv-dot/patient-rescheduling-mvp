"""Available and unavailable slots for a reschedule (BR-04, BR-05, BR-06)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import OFFICE_TZ
from rules.business_rules import (
    SlotInfo,
    needs_fallback,
    slot_display_window,
    slot_selectability,
    slots_in_window,
    validate_new_slot,
)
from services.appointment_service import AppointmentRecord

_SLOTS_SQL = """
SELECT s.slot_id, s.provider_id, s.type_id, s.start_time, s.end_time, s.status,
       p.display_name AS provider_name, l.name AS location_name
FROM slots s
JOIN providers p ON p.provider_id = s.provider_id
JOIN locations l ON l.location_id = s.location_id
WHERE s.type_id = %s AND s.start_time >= %s AND s.start_time < %s
"""

_OTHER_RANGES_SQL = """
SELECT s.start_time, s.end_time
FROM appointments a
JOIN slots s ON s.slot_id = a.slot_id
WHERE a.patient_id = %s AND a.appt_id <> %s
  AND a.status IN ('Scheduled', 'Confirmed', 'Rescheduled')
"""

FALLBACK_MESSAGE = (
    "Only a few times are open for this type of appointment. "
    "Please try again later or call the office."
)


def other_active_ranges(conn: Any, record: AppointmentRecord) -> list[tuple[datetime, datetime]]:
    """Time ranges of the patient's other active appointments (to avoid overlaps)."""
    rows = conn.execute(_OTHER_RANGES_SQL, (record.patient_id, record.appt_id)).fetchall()
    return [(r["start_time"], r["end_time"]) for r in rows]


def list_slots(conn: Any, record: AppointmentRecord, now: datetime) -> dict[str, Any]:
    """Slots of the same type in the next 14 days, in time order, with selectability."""
    window_start, window_end = slot_display_window(now)
    rows = conn.execute(_SLOTS_SQL, (record.type_id, window_start, window_end)).fetchall()
    names = {r["slot_id"]: (r["provider_name"], r["location_name"]) for r in rows}
    infos = slots_in_window(
        (
            SlotInfo(
                r["slot_id"],
                r["provider_id"],
                r["type_id"],
                r["start_time"],
                r["end_time"],
                r["status"],
            )
            for r in rows
        ),
        now,
    )
    others = other_active_ranges(conn, record)
    appointment = record.info()
    office_tz = ZoneInfo(OFFICE_TZ)

    slots: list[dict[str, Any]] = []
    available = 0
    for slot in infos:
        selectable, reason = slot_selectability(slot, now)
        if slot.slot_id == record.slot_id:
            selectable, reason = False, "current"
        elif selectable:
            check = validate_new_slot(slot, appointment, now, others)
            if not check.ok:
                selectable, reason = False, "conflict" if check.code == "overlap" else "unavailable"
        available += 1 if selectable else 0
        provider_name, location_name = names[slot.slot_id]
        slots.append(
            {
                "slot_id": slot.slot_id,
                "provider": provider_name,
                "location": location_name,
                "start_time": slot.start_time.astimezone(office_tz),
                "selectable": selectable,
                "reason": reason,
            }
        )

    fallback = needs_fallback(available)
    return {
        "slots": slots,
        "available_count": available,
        "needs_fallback": fallback,
        "message": FALLBACK_MESSAGE if fallback else None,
    }
