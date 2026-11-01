"""Estimated wait by appointment type and provider (BR-12)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import OFFICE_TZ
from rules.business_rules import (
    SlotInfo,
    earliest_available_by_provider,
    estimate_wait,
    format_wait,
    soonest_provider,
)
from services.exceptions import NotFoundError

ESTIMATE_NOTE = (
    "Estimated from demo data: the time until each provider's next open time. It is not a promise."
)

_SLOTS_SQL = """
SELECT s.slot_id, s.provider_id, s.type_id, s.start_time, s.end_time, s.status,
       p.display_name AS provider_name, l.name AS location_name
FROM slots s
JOIN providers p ON p.provider_id = s.provider_id
JOIN locations l ON l.location_id = s.location_id
WHERE s.status = 'Available' AND s.start_time > %s
"""


def estimates(conn: Any, now: datetime, type_id: Optional[str] = None) -> dict[str, Any]:
    """Wait estimates per provider for one type, or for every type when none is given."""
    if type_id is None:
        types = conn.execute(
            "SELECT type_id, label FROM appointment_types ORDER BY type_id"
        ).fetchall()
    else:
        types = conn.execute(
            "SELECT type_id, label FROM appointment_types WHERE type_id = %s", (type_id,)
        ).fetchall()
        if not types:
            raise NotFoundError(type_id)

    sql, params = _SLOTS_SQL, [now]
    if type_id is not None:
        sql, params = sql + " AND s.type_id = %s", [now, type_id]
    rows = conn.execute(sql, params).fetchall()

    office_tz = ZoneInfo(OFFICE_TZ)
    infos = [
        SlotInfo(
            r["slot_id"],
            r["provider_id"],
            r["type_id"],
            r["start_time"],
            r["end_time"],
            r["status"],
        )
        for r in rows
    ]
    names = {r["slot_id"]: (r["provider_name"], r["location_name"]) for r in rows}

    result_types = []
    for type_row in types:
        of_type = [s for s in infos if s.type_id == type_row["type_id"]]
        earliest = earliest_available_by_provider(of_type, now, type_row["type_id"])
        soonest = soonest_provider(earliest)
        # Find the slot that gives each provider's earliest time (for names and place).
        first_slot = {}
        for slot in of_type:
            if earliest.get(slot.provider_id) == slot.start_time:
                first_slot.setdefault(slot.provider_id, slot.slot_id)
        providers = []
        for provider_id, start in sorted(earliest.items(), key=lambda kv: (kv[1], kv[0])):
            provider_name, location_name = names[first_slot[provider_id]]
            providers.append(
                {
                    "provider": provider_name,
                    "location": location_name,
                    "earliest_start": start.astimezone(office_tz),
                    "wait_label": format_wait(estimate_wait(start, now)),
                    "is_soonest": provider_id == soonest,
                }
            )
        result_types.append(
            {"type_id": type_row["type_id"], "label": type_row["label"], "providers": providers}
        )
    return {"estimate_note": ESTIMATE_NOTE, "types": result_types}
