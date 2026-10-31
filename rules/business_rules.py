"""Pure business rules for the Patient Rescheduling MVP.

No database, network or clock access: every function receives what it needs,
including ``now``. All datetimes must be timezone-aware. Rule IDs (BR-xx) refer
to the Scrum plan document.

How the 25 rules are covered
----------------------------
A rule can only live fully in this module if it can be decided from inputs
alone. Rules that also need a database transaction, an HTTP response or a
browser are split: the *decision logic* is here (and unit tested), and the layer
that *applies* it is named in ``BR_ENFORCEMENT`` at the bottom of this file. A
guard test fails if any rule has no owner or names a function that does not exist.

Interpretation notes (please confirm with the product owner):

* BR-07 (24-hour lock) blocks patient *rescheduling* inside 24 hours. Patient
  *cancellation* inside 24 hours is still allowed but is flagged as a late
  cancellation (BR-11). Confirming is always allowed for active appointments.
  Suggested wording for BR-07: "No patient reschedule within 24 hours."
* BR-10 (no double assignment) is enforced by a database unique index and an
  atomic update in the service layer. ``validate_new_slot`` is the friendly
  pre-check, and ``find_double_assigned_slots`` is a consistency check for tests.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable, Mapping, Optional, Sequence

from config.settings import (
    DELAY_THRESHOLD_MIN,
    DEMO_BANNER_TEXT,
    LOCK_HOURS,
    LOCKOUT_ATTEMPTS,
    LOCKOUT_MINUTES,
    MAX_RESCHEDULES,
    MIN_SLOTS,
    REMINDER_HOURS_BEFORE,
    SECURITY_HEADERS,
    SLOT_WINDOW_DAYS,
    UTIL_HIGH,
    UTIL_LOW,
)

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


class AppointmentStatus(StrEnum):
    """BR-01 appointment statuses."""

    SCHEDULED = "Scheduled"
    CONFIRMED = "Confirmed"
    RESCHEDULED = "Rescheduled"
    CANCELLED = "Cancelled"
    COMPLETED = "Completed"
    NO_SHOW = "No-show"


class SlotStatus(StrEnum):
    AVAILABLE = "Available"
    BOOKED = "Booked"
    BLOCKED = "Blocked"


class Role(StrEnum):
    """BR-15 roles."""

    PATIENT = "Patient"
    FRONT_DESK = "FrontDesk"
    MANAGER = "Manager"


class ConsentType(StrEnum):
    NOTICE = "C1"  # privacy notice and demo terms
    REMINDERS = "C2"  # simulated reminder preference
    STAFF_ACK = "C3"  # staff confidentiality acknowledgment


class ConsentStatus(StrEnum):
    GRANTED = "Granted"
    DECLINED = "Declined"
    WITHDRAWN = "Withdrawn"


ACTIVE_STATUSES = frozenset(
    {AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED, AppointmentStatus.RESCHEDULED}
)
TERMINAL_STATUSES = frozenset(
    {AppointmentStatus.CANCELLED, AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}
)

# Plain-language messages. Error codes map to HTTP statuses in the API layer.
MESSAGES: Mapping[str, str] = {
    "invalid_state": "This appointment can no longer be changed online.",
    "too_late": "This appointment is too close to change online. Please call the office.",
    "limit_reached": "This appointment has already been rescheduled the maximum number of times.",
    "slot_unavailable": "That time is no longer available. Please choose another.",
    "slot_past": "That time has already passed. Please choose another.",
    "wrong_type": "That time is for a different type of appointment.",
    "overlap": "That time overlaps with another appointment you have.",
    "same_slot": "That is your current appointment time. Please choose a different time.",
    "slot_taken": "That time was just taken. Please choose another.",
    "consent_required": "Please review and accept the privacy notice to continue.",
    "not_found": "We could not find a match. Please check your details and try again.",
    "locked": "Too many attempts. Please wait a few minutes and try again.",
    "forbidden": "You do not have access to this page.",
    "unavailable": "The service is temporarily unavailable. Please try again shortly.",
}


@dataclass(frozen=True)
class RuleResult:
    """Outcome of a rule check: allowed, or denied with a code and message."""

    ok: bool
    code: Optional[str] = None
    message: Optional[str] = None

    @classmethod
    def allow(cls) -> RuleResult:
        return cls(ok=True)

    @classmethod
    def deny(cls, code: str) -> RuleResult:
        return cls(ok=False, code=code, message=MESSAGES[code])


@dataclass(frozen=True)
class SlotInfo:
    """The slot fields the rules need."""

    slot_id: str
    provider_id: str
    type_id: str
    start_time: datetime
    end_time: datetime
    status: str


@dataclass(frozen=True)
class AppointmentInfo:
    """The appointment fields the rules need."""

    appt_id: str
    status: str
    type_id: str
    slot_id: str
    start_time: datetime
    reschedule_count: int = 0


def _require_aware(*values: datetime) -> None:
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime values must be timezone-aware.")


# --------------------------------------------------------------------------
# BR-01, BR-02: status lifecycle
# --------------------------------------------------------------------------


def is_active(status: str) -> bool:
    """True for Scheduled, Confirmed or Rescheduled."""
    return AppointmentStatus(status) in ACTIVE_STATUSES


def is_terminal(status: str) -> bool:
    """True for Cancelled, Completed or No-show."""
    return AppointmentStatus(status) in TERMINAL_STATUSES


def can_confirm(status: str) -> RuleResult:
    """BR-02: only active appointments can be confirmed."""
    return RuleResult.allow() if is_active(status) else RuleResult.deny("invalid_state")


# --------------------------------------------------------------------------
# BR-07, BR-08, BR-11: lock window, reschedule limit, cancellation
# --------------------------------------------------------------------------


def within_lock_window(start_time: datetime, now: datetime, lock_hours: int = LOCK_HOURS) -> bool:
    """True when the appointment starts in under ``lock_hours`` (or has started)."""
    _require_aware(start_time, now)
    return (start_time - now) < timedelta(hours=lock_hours)


def can_reschedule(
    status: str,
    start_time: datetime,
    reschedule_count: int,
    now: datetime,
) -> RuleResult:
    """BR-02, BR-07, BR-08: may the patient reschedule this appointment?"""
    if not is_active(status):
        return RuleResult.deny("invalid_state")
    if within_lock_window(start_time, now):
        return RuleResult.deny("too_late")
    if reschedule_count >= MAX_RESCHEDULES:
        return RuleResult.deny("limit_reached")
    return RuleResult.allow()


def can_cancel(status: str, start_time: datetime, now: datetime) -> RuleResult:
    """BR-02, BR-11: active and not yet started. Late cancels are allowed but flagged."""
    _require_aware(start_time, now)
    if not is_active(status):
        return RuleResult.deny("invalid_state")
    if start_time <= now:
        return RuleResult.deny("too_late")
    return RuleResult.allow()


def is_late_cancellation(start_time: datetime, now: datetime) -> bool:
    """BR-11: cancelled inside the lock window but before the start time."""
    _require_aware(start_time, now)
    return now < start_time and within_lock_window(start_time, now)


def patient_actions(
    status: str, start_time: datetime, reschedule_count: int, now: datetime
) -> dict[str, RuleResult]:
    """What the patient page may enable, with a reason for each disabled action."""
    return {
        "confirm": can_confirm(status),
        "reschedule": can_reschedule(status, start_time, reschedule_count, now),
        "cancel": can_cancel(status, start_time, now),
    }


# --------------------------------------------------------------------------
# BR-04, BR-05, BR-06: slots
# --------------------------------------------------------------------------


def slot_display_window(now: datetime, days: int = SLOT_WINDOW_DAYS) -> tuple[datetime, datetime]:
    """BR-04: from the start of today (office time) to ``days`` days ahead."""
    _require_aware(now)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, now + timedelta(days=days)


def slot_selectability(slot: SlotInfo, now: datetime) -> tuple[bool, Optional[str]]:
    """BR-05: (selectable, reason). Reason is booked, blocked or past when disabled."""
    _require_aware(slot.start_time, now)
    status = SlotStatus(slot.status)
    if status is SlotStatus.BOOKED:
        return False, "booked"
    if status is SlotStatus.BLOCKED:
        return False, "blocked"
    if slot.start_time <= now:
        return False, "past"
    return True, None


def order_slots(slots: Iterable[SlotInfo]) -> list[SlotInfo]:
    """BR-04: chronological order (ties broken by slot ID for stable output)."""
    return sorted(slots, key=lambda s: (s.start_time, s.slot_id))


def slots_in_window(
    slots: Iterable[SlotInfo], now: datetime, days: int = SLOT_WINDOW_DAYS
) -> list[SlotInfo]:
    """Slots inside the display window, in chronological order."""
    window_start, window_end = slot_display_window(now, days)
    inside = [s for s in slots if window_start <= s.start_time < window_end]
    return order_slots(inside)


def needs_fallback(available_count: int, minimum: int = MIN_SLOTS) -> bool:
    """BR-04: True when fewer than ``minimum`` slots are available."""
    return available_count < minimum


def validate_new_slot(
    slot: SlotInfo,
    appointment: AppointmentInfo,
    now: datetime,
    other_active_ranges: Sequence[tuple[datetime, datetime]] = (),
) -> RuleResult:
    """BR-06: friendly pre-check of a chosen replacement slot.

    ``other_active_ranges`` are (start, end) pairs of the patient's *other*
    active appointments, used to prevent overlapping bookings.
    """
    if slot.slot_id == appointment.slot_id:
        return RuleResult.deny("same_slot")
    selectable, reason = slot_selectability(slot, now)
    if not selectable:
        return RuleResult.deny("slot_past" if reason == "past" else "slot_unavailable")
    if slot.type_id != appointment.type_id:
        return RuleResult.deny("wrong_type")
    for other_start, other_end in other_active_ranges:
        if slot.start_time < other_end and other_start < slot.end_time:
            return RuleResult.deny("overlap")
    return RuleResult.allow()


# --------------------------------------------------------------------------
# BR-12: estimated wait
# --------------------------------------------------------------------------


def earliest_available_by_provider(
    slots: Iterable[SlotInfo], now: datetime, type_id: Optional[str] = None
) -> dict[str, datetime]:
    """Earliest selectable start time per provider (optionally for one type)."""
    earliest: dict[str, datetime] = {}
    for slot in slots:
        if type_id is not None and slot.type_id != type_id:
            continue
        selectable, _ = slot_selectability(slot, now)
        if not selectable:
            continue
        current = earliest.get(slot.provider_id)
        if current is None or slot.start_time < current:
            earliest[slot.provider_id] = slot.start_time
    return earliest


def estimate_wait(start_time: datetime, now: datetime) -> timedelta:
    """Time from now to a slot start; never negative."""
    _require_aware(start_time, now)
    return max(start_time - now, timedelta(0))


def soonest_provider(estimates: Mapping[str, datetime]) -> Optional[str]:
    """Provider with the earliest slot (ties broken by provider ID)."""
    if not estimates:
        return None
    return min(estimates, key=lambda provider_id: (estimates[provider_id], provider_id))


def format_wait(wait: timedelta) -> str:
    """Plain-language wait such as '45 minutes', '3 hours' or '2 days'."""
    minutes = int(wait.total_seconds() // 60)
    if minutes < 1:
        return "Now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


# --------------------------------------------------------------------------
# BR-13: delays
# --------------------------------------------------------------------------


def compute_delay_minutes(scheduled_start: datetime, actual_start: datetime) -> int:
    """Whole minutes late; arriving early counts as 0."""
    _require_aware(scheduled_start, actual_start)
    return max(int((actual_start - scheduled_start).total_seconds() // 60), 0)


def is_delayed(delay_minutes: int, threshold: int = DELAY_THRESHOLD_MIN) -> bool:
    """BR-13: delayed when strictly more than the threshold."""
    return delay_minutes > threshold


# --------------------------------------------------------------------------
# BR-14: simulated reminders
# --------------------------------------------------------------------------


def reminder_due_times(
    start_time: datetime, hours_before: Sequence[int] = REMINDER_HOURS_BEFORE
) -> list[datetime]:
    """Due times for simulated reminders, earliest first."""
    _require_aware(start_time)
    return sorted(start_time - timedelta(hours=h) for h in hours_before)


# --------------------------------------------------------------------------
# BR-18, BR-19, BR-24: consent
# --------------------------------------------------------------------------


def c1_satisfied(
    latest_status: Optional[str], latest_version: Optional[str], current_version: str
) -> bool:
    """BR-18: C1 granted for the *current* notice version."""
    return latest_status == ConsentStatus.GRANTED.value and latest_version == current_version


def reminders_enabled(c2_latest_status: Optional[str]) -> bool:
    """BR-19: reminders are opt-in; any status other than Granted means off."""
    return c2_latest_status == ConsentStatus.GRANTED.value


def c3_satisfied(acknowledged_this_session: bool) -> bool:
    """BR-24: staff must acknowledge confidentiality every session."""
    return bool(acknowledged_this_session)


# --------------------------------------------------------------------------
# BR-15: role permissions (deny by default)
# --------------------------------------------------------------------------

_STAFF_PERMISSIONS = frozenset(
    {
        "staff:schedule:read",
        "staff:open_slots:read",
        "staff:appointment:update",
        "staff:appointment:reschedule",
        "staff:appointment:cancel",
    }
)
_MANAGER_ONLY = frozenset({"manager:kpis:read", "manager:audit:read", "manager:consents:read"})

ROLE_PERMISSIONS: Mapping[Role, frozenset[str]] = {
    Role.PATIENT: frozenset(
        {
            "appointment:read_own",
            "appointment:act_own",
            "slots:read_own",
            "consent:write_own",
        }
    ),
    Role.FRONT_DESK: _STAFF_PERMISSIONS,
    Role.MANAGER: _STAFF_PERMISSIONS | _MANAGER_ONLY,
}


def is_allowed(role: str, permission: str) -> bool:
    """BR-15: True only when the role explicitly holds the permission."""
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False


# --------------------------------------------------------------------------
# BR-20: sign-in lockout
# --------------------------------------------------------------------------


def is_locked(locked_until: Optional[datetime], now: datetime) -> bool:
    """True while a sign-in lockout is still in effect."""
    _require_aware(now)
    return locked_until is not None and now < locked_until


def register_failed_attempt(failed_attempts: int, now: datetime) -> tuple[int, Optional[datetime]]:
    """Return (new_failed_attempts, locked_until) after one more failure.

    The Nth failure sets a lockout and resets the counter, so the next window
    starts fresh once the lockout expires.
    """
    _require_aware(now)
    attempts = failed_attempts + 1
    if attempts >= LOCKOUT_ATTEMPTS:
        return 0, now + timedelta(minutes=LOCKOUT_MINUTES)
    return attempts, None


# --------------------------------------------------------------------------
# Dashboard helpers (US-06)
# --------------------------------------------------------------------------


def safe_rate(numerator: int, denominator: int) -> float:
    """Ratio that returns 0.0 instead of dividing by zero."""
    return numerator / denominator if denominator else 0.0


def utilization_ratio(booked: int, capacity: int) -> float:
    """Booked slots divided by bookable capacity (blocked slots excluded)."""
    return safe_rate(booked, capacity)


def utilization_flag(ratio: float) -> str:
    """Staffing signal: 'low' under 60 percent, 'high' over 90 percent, else 'normal'."""
    if ratio < UTIL_LOW:
        return "low"
    if ratio > UTIL_HIGH:
        return "high"
    return "normal"


# --------------------------------------------------------------------------
# BR-09, BR-11: reschedule and cancel plans
# Pure decisions. The service applies a plan inside ONE database transaction.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReschedulePlan:
    """Everything that must change, together or not at all (BR-09)."""

    appt_id: str
    old_slot_id: str
    new_slot_id: str
    old_start: datetime
    new_start: datetime
    new_reschedule_count: int
    new_status: str = AppointmentStatus.RESCHEDULED.value
    old_slot_status: str = SlotStatus.AVAILABLE.value  # old slot is released
    new_slot_status: str = SlotStatus.BOOKED.value  # new slot is claimed


@dataclass(frozen=True)
class CancelPlan:
    """What a cancellation changes (BR-11)."""

    appt_id: str
    slot_id: str
    late_cancellation: bool
    new_status: str = AppointmentStatus.CANCELLED.value
    slot_status: str = SlotStatus.AVAILABLE.value  # slot is released immediately


def plan_reschedule(
    appointment: AppointmentInfo,
    new_slot: SlotInfo,
    now: datetime,
    other_active_ranges: Sequence[tuple[datetime, datetime]] = (),
) -> tuple[RuleResult, Optional[ReschedulePlan]]:
    """Check BR-02, 06, 07, 08 and return the plan to apply, or the denial."""
    result = can_reschedule(
        appointment.status, appointment.start_time, appointment.reschedule_count, now
    )
    if not result.ok:
        return result, None
    result = validate_new_slot(new_slot, appointment, now, other_active_ranges)
    if not result.ok:
        return result, None
    return RuleResult.allow(), ReschedulePlan(
        appt_id=appointment.appt_id,
        old_slot_id=appointment.slot_id,
        new_slot_id=new_slot.slot_id,
        old_start=appointment.start_time,
        new_start=new_slot.start_time,
        new_reschedule_count=appointment.reschedule_count + 1,
    )


def plan_cancel(
    appointment: AppointmentInfo, now: datetime
) -> tuple[RuleResult, Optional[CancelPlan]]:
    """Check BR-02 and BR-11 and return the cancellation plan, or the denial."""
    result = can_cancel(appointment.status, appointment.start_time, now)
    if not result.ok:
        return result, None
    return RuleResult.allow(), CancelPlan(
        appt_id=appointment.appt_id,
        slot_id=appointment.slot_id,
        late_cancellation=is_late_cancellation(appointment.start_time, now),
    )


def find_double_assigned_slots(appointments: Iterable[AppointmentInfo]) -> set[str]:
    """BR-10 consistency check: slot IDs held by more than one active appointment."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for appointment in appointments:
        if is_active(appointment.status):
            if appointment.slot_id in seen:
                duplicates.add(appointment.slot_id)
            seen.add(appointment.slot_id)
    return duplicates


# --------------------------------------------------------------------------
# BR-03, BR-21: minimum necessary data (response field whitelists)
# --------------------------------------------------------------------------

# BR-03 content fields are start_time (date and time), time_zone, location, label
# and status. appt_id and allowed_actions are identifiers and UI flags, not content.
ALLOWED_RESPONSE_FIELDS: Mapping[str, frozenset[str]] = {
    "patient_appointment": frozenset(
        {"appt_id", "start_time", "time_zone", "location", "label", "status", "allowed_actions"}
    ),
    "patient_slot": frozenset(
        {"slot_id", "provider", "location", "start_time", "selectable", "reason"}
    ),
    "patient_wait_estimate": frozenset(
        {"provider", "location", "earliest_start", "wait_label", "is_soonest"}
    ),
    "staff_schedule_row": frozenset(
        {
            "appt_id",
            "patient_display_name",
            "provider",
            "location",
            "label",
            "start_time",
            "status",
            "delay_minutes",
        }
    ),
}

# Field-name parts that must never appear in the schema, API or data (privacy scan).
PROHIBITED_FIELD_PARTS = frozenset(
    {
        "phone",
        "email",
        "mrn",
        "diagnosis",
        "diagnoses",
        "insurance",
        "ssn",
        "address",
        "birth",
        "dob",
        "street",
        "zip",
        "medical",
        "prescription",
        "medication",
    }
)


def has_prohibited_field_name(name: str) -> bool:
    """True if a field or column name contains a prohibited part (for example 'phone')."""
    return bool(set(name.lower().split("_")) & PROHIBITED_FIELD_PARTS)


def disallowed_fields(response_name: str, data: Mapping[str, object]) -> set[str]:
    """Fields in ``data`` that are not on the whitelist. Unknown names raise KeyError."""
    return set(data) - ALLOWED_RESPONSE_FIELDS[response_name]


def restrict_to_allowed(response_name: str, data: Mapping[str, object]) -> dict[str, object]:
    """Copy of ``data`` limited to whitelisted fields (deny by default)."""
    allowed = ALLOWED_RESPONSE_FIELDS[response_name]
    return {key: value for key, value in data.items() if key in allowed}


# --------------------------------------------------------------------------
# BR-16: audit entries
# --------------------------------------------------------------------------


class AuditAction(StrEnum):
    """The only actions that may be written to the audit log."""

    APPOINTMENT_VIEW = "appointment.view"  # staff opened appointment detail
    APPOINTMENT_CONFIRM = "appointment.confirm"
    APPOINTMENT_CANCEL = "appointment.cancel"
    APPOINTMENT_RESCHEDULE = "appointment.reschedule"
    APPOINTMENT_STATUS_UPDATE = "appointment.status_update"
    CONSENT_GRANT = "consent.grant"
    CONSENT_DECLINE = "consent.decline"
    CONSENT_WITHDRAW = "consent.withdraw"
    STAFF_ACKNOWLEDGE = "staff.acknowledge"
    ACCESS_DENIED = "access.denied"
    SIGN_IN_FAILED = "auth.failed"
    SIGN_IN_LOCKED = "auth.locked"


@dataclass(frozen=True)
class AuditEntry:
    """A validated audit record: role, action and (optionally) a synthetic appointment ID."""

    actor_role: str
    action: str
    appt_id: Optional[str] = None


def audit_entry(role: str, action: str, appt_id: Optional[str] = None) -> AuditEntry:
    """Build an audit entry; unknown roles or actions raise ValueError."""
    return AuditEntry(Role(role).value, AuditAction(action).value, appt_id)


# --------------------------------------------------------------------------
# BR-17, BR-22: demo banner, security headers, safe error bodies
# --------------------------------------------------------------------------

_INTERNAL_MARKERS = (
    "traceback",
    'file "',
    ".py",
    "psycopg",
    "postgres",
    "select ",
    "insert into",
    "password",
)


def demo_banner_present(page_text: str) -> bool:
    """BR-17: True when the DEMO banner text appears in rendered page text."""
    return DEMO_BANNER_TEXT in page_text


def missing_security_headers(
    headers: Mapping[str, str], appointment_data: bool = False
) -> list[str]:
    """BR-22: names of required headers that are absent (case-insensitive)."""
    lowered = {name.lower(): value for name, value in headers.items()}
    missing = [name for name in SECURITY_HEADERS if name.lower() not in lowered]
    if appointment_data and "no-store" not in lowered.get("cache-control", "").lower():
        missing.append("Cache-Control: no-store")
    return missing


def contains_internal_details(text: str) -> bool:
    """BR-22: True if text looks like a stack trace, SQL, path or credential detail."""
    lowered = text.lower()
    return any(marker in lowered for marker in _INTERNAL_MARKERS)


def error_body(code: str, message: Optional[str] = None) -> dict[str, dict[str, str]]:
    """BR-22: the standard error format, with a plain-language message."""
    text = message or MESSAGES.get(code, "Something went wrong. Please try again.")
    if contains_internal_details(text):
        raise ValueError("Error messages must not contain internal details.")
    return {"error": {"code": code, "message": text}}


# --------------------------------------------------------------------------
# BR-23: theme and contrast
# --------------------------------------------------------------------------

AA_TEXT = 4.5
AA_LARGE_TEXT = 3.0
AA_COMPONENT = 3.0
_AA_MINIMUMS = {"text": AA_TEXT, "large_text": AA_LARGE_TEXT, "component": AA_COMPONENT}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Colors must be hex, such as #1a2b3c.")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a hex color."""

    def channel(raw: int) -> float:
        c = raw / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colors (1.0 to 21.0)."""
    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def meets_wcag_aa(foreground: str, background: str, kind: str = "text") -> bool:
    """BR-23: AA needs 4.5:1 for text and 3:1 for large text or interface components."""
    return contrast_ratio(foreground, background) >= _AA_MINIMUMS[kind]


def resolve_theme(stored: Optional[str], system_prefers_dark: bool) -> str:
    """BR-23: a saved choice wins; otherwise follow the system preference."""
    if stored in ("light", "dark"):
        return stored
    return "dark" if system_prefers_dark else "light"


# --------------------------------------------------------------------------
# BR-25: idempotent writes
# --------------------------------------------------------------------------

_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,64}")


def is_valid_idempotency_key(key: object) -> bool:
    """16 to 64 letters, digits, hyphens or underscores (a UUID qualifies)."""
    return isinstance(key, str) and _IDEMPOTENCY_KEY.fullmatch(key) is not None


def request_fingerprint(endpoint: str, payload: Mapping[str, object]) -> str:
    """Stable hash of a request, so a reused key with a different body is detectable."""
    canonical = json.dumps(
        {"endpoint": endpoint, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_outcome(stored_fingerprint: Optional[str], new_fingerprint: str) -> str:
    """'new' (run it), 'replay' (return the saved result) or 'conflict' (key reused)."""
    if stored_fingerprint is None:
        return "new"
    return "replay" if stored_fingerprint == new_fingerprint else "conflict"


# --------------------------------------------------------------------------
# Rule coverage map: which layer applies each rule
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Enforcement:
    """Where a rule is decided and applied."""

    summary: str
    owners: tuple[str, ...]  # files that apply the rule
    batch: int  # build batch in which enforcement is complete
    symbols: tuple[str, ...] = ()  # functions or constants in THIS module


BR_ENFORCEMENT: Mapping[str, Enforcement] = {
    "BR-01": Enforcement(
        "Status lifecycle",
        ("rules/business_rules.py",),
        1,
        ("AppointmentStatus", "is_active", "is_terminal"),
    ),
    "BR-02": Enforcement(
        "Only active appointments can be acted on",
        ("rules/business_rules.py",),
        1,
        ("can_confirm", "can_reschedule", "can_cancel"),
    ),
    "BR-03": Enforcement(
        "Patient page shows minimum necessary fields",
        ("rules/business_rules.py", "api/schemas.py"),
        2,
        ("ALLOWED_RESPONSE_FIELDS", "disallowed_fields", "restrict_to_allowed"),
    ),
    "BR-04": Enforcement(
        "Chronological slots, 14-day window, at least 3 or fallback",
        ("rules/business_rules.py", "services/slot_service.py"),
        2,
        ("slots_in_window", "order_slots", "needs_fallback"),
    ),
    "BR-05": Enforcement(
        "Unavailable slots shown but not selectable",
        ("rules/business_rules.py", "services/slot_service.py", "static/patient/js/app.js"),
        3,
        ("slot_selectability",),
    ),
    "BR-06": Enforcement(
        "New slot must be available, future and the same type",
        ("rules/business_rules.py", "services/reschedule_service.py"),
        2,
        ("validate_new_slot",),
    ),
    "BR-07": Enforcement(
        "No patient reschedule within 24 hours",
        ("rules/business_rules.py", "services/reschedule_service.py"),
        2,
        ("within_lock_window", "can_reschedule"),
    ),
    "BR-08": Enforcement(
        "Maximum 2 reschedules",
        ("rules/business_rules.py", "services/reschedule_service.py"),
        2,
        ("can_reschedule",),
    ),
    "BR-09": Enforcement(
        "Reschedule is one atomic transaction",
        ("rules/business_rules.py", "services/reschedule_service.py"),
        2,
        ("ReschedulePlan", "plan_reschedule"),
    ),
    "BR-10": Enforcement(
        "A slot is never assigned twice",
        ("db/schema.sql", "services/reschedule_service.py"),
        2,
        ("validate_new_slot", "find_double_assigned_slots"),
    ),
    "BR-11": Enforcement(
        "Cancellation confirmed, slot released, late flag",
        ("rules/business_rules.py", "services/appointment_service.py"),
        2,
        ("can_cancel", "is_late_cancellation", "CancelPlan", "plan_cancel"),
    ),
    "BR-12": Enforcement(
        "Estimated wait to earliest available slot",
        ("rules/business_rules.py", "services/wait_time_service.py"),
        2,
        ("earliest_available_by_provider", "estimate_wait", "soonest_provider", "format_wait"),
    ),
    "BR-13": Enforcement(
        "Delay and delayed threshold",
        ("rules/business_rules.py", "services/frontdesk_service.py"),
        4,
        ("compute_delay_minutes", "is_delayed"),
    ),
    "BR-14": Enforcement(
        "Simulated reminders at 48 and 24 hours",
        ("rules/business_rules.py", "services/reminder_service.py"),
        2,
        ("reminder_due_times",),
    ),
    "BR-15": Enforcement(
        "Role permissions, deny by default",
        ("rules/business_rules.py", "api/deps.py"),
        2,
        ("ROLE_PERMISSIONS", "is_allowed"),
    ),
    "BR-16": Enforcement(
        "Audit every change",
        ("rules/business_rules.py", "db/schema.sql", "services/audit_service.py"),
        4,
        ("AuditAction", "audit_entry"),
    ),
    "BR-17": Enforcement(
        "DEMO banner on every page; no free text",
        ("config/settings.py", "static/patient/index.html", "streamlit_app.py"),
        3,
        ("demo_banner_present",),
    ),
    "BR-18": Enforcement(
        "Consent C1 before any appointment data",
        ("rules/business_rules.py", "services/consent_service.py", "api/deps.py"),
        2,
        ("c1_satisfied",),
    ),
    "BR-19": Enforcement(
        "Reminders are opt-in and withdrawable",
        ("rules/business_rules.py", "services/reminder_service.py"),
        2,
        ("reminders_enabled",),
    ),
    "BR-20": Enforcement(
        "Generic sign-in failure and lockout",
        ("rules/business_rules.py", "api/security.py"),
        2,
        ("is_locked", "register_failed_attempt"),
    ),
    "BR-21": Enforcement(
        "Whitelisted response schemas",
        ("rules/business_rules.py", "api/schemas.py"),
        2,
        ("ALLOWED_RESPONSE_FIELDS", "disallowed_fields", "has_prohibited_field_name"),
    ),
    "BR-22": Enforcement(
        "No-store, security headers, no internals in errors",
        ("rules/business_rules.py", "api/main.py", "api/errors.py"),
        2,
        ("missing_security_headers", "contains_internal_details", "error_body"),
    ),
    "BR-23": Enforcement(
        "Theme follows system, toggle remembered, AA contrast",
        ("rules/business_rules.py", "static/patient/css/theme.css", "static/patient/js/theme.js"),
        3,
        ("contrast_ratio", "meets_wcag_aa", "resolve_theme"),
    ),
    "BR-24": Enforcement(
        "Staff acknowledge confidentiality each session",
        ("rules/business_rules.py", "api/routers/staff.py", "streamlit_app.py"),
        4,
        ("c3_satisfied",),
    ),
    "BR-25": Enforcement(
        "Idempotency-Key on write endpoints",
        ("rules/business_rules.py", "services/reschedule_service.py", "db/schema.sql"),
        2,
        ("is_valid_idempotency_key", "request_fingerprint", "idempotency_outcome"),
    ),
}
