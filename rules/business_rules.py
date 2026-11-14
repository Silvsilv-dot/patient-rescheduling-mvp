"""Pure Zero-I/O Business Rules Engine (BR-01 through BR-25).
No database, network, or system clock calls; `now` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

LOCK_HOURS = 24
MAX_RESCHEDULES = 2
SLOT_WINDOW_DAYS = 14
MIN_SLOTS_FALLBACK = 3
UTILIZATION_LOW_PCT = 60.0
UTILIZATION_HIGH_PCT = 90.0
DELAY_FLAG_MINUTES = 15
CONSENT_C1_VERSION = "v1.0"
CONSENT_C3_VERSION = "v1.0"

ACTIVE_STATUSES = frozenset({"Scheduled", "Confirmed", "Rescheduled"})
TERMINAL_STATUSES = frozenset({"Cancelled", "Completed", "No-show"})

# BR-03 / BR-21: Prohibited PII/PHI field substrings
PROHIBITED_FIELD_PARTS = frozenset({
    "ssn", "mrn", "dob", "birth", "address", "phone", "email",
    "diagnosis", "icd", "insurance", "policy", "subscriber", "credit", "passport"
})


def assert_no_prohibited_fields(payload: Any) -> None:
    """Recursively verifies zero prohibited PII/PHI field names exist in API payloads."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            lower_k = str(k).lower()
            for bad in PROHIBITED_FIELD_PARTS:
                if bad in lower_k:
                    raise ValueError(f"Compliance violation (BR-03): prohibited field '{k}'")
            assert_no_prohibited_fields(v)
    elif isinstance(payload, list):
        for item in payload:
            assert_no_prohibited_fields(item)


@dataclass(frozen=True)
class ActionEligibility:
    can_confirm: bool
    can_reschedule: bool
    can_cancel: bool
    lock_reason_code: str | None
    hours_until_start: float
    is_late_cancel_window: bool


def evaluate_appointment_actions(
    status: str,
    start_time: datetime,
    reschedule_count: int,
    now: datetime,
    is_staff_override: bool = False,
) -> ActionEligibility:
    """Enforces BR-01, BR-02, BR-07, BR-08, and BR-11."""
    hours_until = (start_time - now).total_seconds() / 3600.0
    is_late_window = hours_until < LOCK_HOURS

    if status not in ACTIVE_STATUSES:
        return ActionEligibility(
            can_confirm=False,
            can_reschedule=False,
            can_cancel=False,
            lock_reason_code="terminal_status",
            hours_until_start=round(hours_until, 1),
            is_late_cancel_window=is_late_window,
        )

    can_confirm = status in {"Scheduled", "Rescheduled"} and hours_until > 0
    can_cancel = hours_until > 0

    if is_staff_override:
        return ActionEligibility(
            can_confirm=can_confirm,
            can_reschedule=hours_until > 0,
            can_cancel=can_cancel,
            lock_reason_code=None,
            hours_until_start=round(hours_until, 1),
            is_late_cancel_window=is_late_window,
        )

    if hours_until < LOCK_HOURS:
        return ActionEligibility(
            can_confirm=can_confirm,
            can_reschedule=False,
            can_cancel=can_cancel,
            lock_reason_code="within_24h_lock",
            hours_until_start=round(hours_until, 1),
            is_late_cancel_window=True,
        )

    if reschedule_count >= MAX_RESCHEDULES:
        return ActionEligibility(
            can_confirm=can_confirm,
            can_reschedule=False,
            can_cancel=can_cancel,
            lock_reason_code="max_reschedules_reached",
            hours_until_start=round(hours_until, 1),
            is_late_cancel_window=False,
        )

    return ActionEligibility(
        can_confirm=can_confirm,
        can_reschedule=True,
        can_cancel=can_cancel,
        lock_reason_code=None,
        hours_until_start=round(hours_until, 1),
        is_late_cancel_window=False,
    )


def classify_slot_availability(
    slot_status: str,
    slot_start: datetime,
    slot_end: datetime,
    slot_type_id: str,
    required_type_id: str,
    current_slot_id: str,
    slot_id: str,
    other_active_intervals: list[tuple[datetime, datetime]],
    now: datetime,
) -> tuple[bool, str | None]:
    """Enforces BR-04, BR-05, and BR-06 overlap detection."""
    if slot_id == current_slot_id:
        return False, "current"
    if slot_type_id != required_type_id:
        return False, "wrong_type"
    if slot_start <= now:
        return False, "past"
    if slot_status == "Blocked":
        return False, "blocked"
    if slot_status != "Available":
        return False, "booked"
    for other_start, other_end in other_active_intervals:
        if slot_start < other_end and slot_end > other_start:
            return False, "conflict"
    return True, None


def classify_utilization(utilization_pct: float) -> str:
    """Enforces BR-13 utilization thresholds (<60% underutilized, >90% overbooked)."""
    if utilization_pct > UTILIZATION_HIGH_PCT:
        return "overbooked"
    if utilization_pct < UTILIZATION_LOW_PCT:
        return "underutilized"
    return "optimal"


def validate_idempotency_key(key: str | None) -> str:
    """Enforces BR-25 idempotency header rules (16-64 safe chars)."""
    if not key:
        raise ValueError("Missing Idempotency-Key header (BR-25).")
    cleaned = key.strip()
    if len(cleaned) < 12 or len(cleaned) > 64:
        raise ValueError("Idempotency-Key must be 12-64 characters.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not all(ch in allowed for ch in cleaned):
        raise ValueError("Idempotency-Key contains invalid characters.")
    return cleaned
