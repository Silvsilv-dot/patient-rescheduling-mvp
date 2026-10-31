"""Unit tests for the core decision rules in rules/business_rules.py.

Plans, whitelists, audit, headers, theme and idempotency rules are tested in
test_rules_plans_and_policies.py; rule ownership is checked in
test_rule_enforcement_map.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rules import business_rules as br

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def make_slot(slot_id="S1", provider="PRV-A", type_id="TYP-01", start=None, status="Available"):
    start = start or NOW + hours(48)
    return br.SlotInfo(slot_id, provider, type_id, start, start + timedelta(minutes=30), status)


def make_appt(status="Scheduled", start=None, count=0, type_id="TYP-01", slot_id="S0"):
    start = start or NOW + hours(72)
    return br.AppointmentInfo("APT-1", status, type_id, slot_id, start, count)


# ---------------------------------------------------------------- BR-01 / BR-02
@pytest.mark.parametrize("status", ["Scheduled", "Confirmed", "Rescheduled"])
def test_active_statuses(status):
    assert br.is_active(status) and not br.is_terminal(status)


@pytest.mark.parametrize("status", ["Cancelled", "Completed", "No-show"])
def test_terminal_statuses(status):
    assert br.is_terminal(status) and not br.is_active(status)


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        br.is_active("Pending")


@pytest.mark.parametrize("status", ["Scheduled", "Confirmed", "Rescheduled"])
def test_can_confirm_active(status):
    assert br.can_confirm(status).ok


@pytest.mark.parametrize("status", ["Cancelled", "Completed", "No-show"])
def test_cannot_confirm_terminal(status):
    result = br.can_confirm(status)
    assert not result.ok and result.code == "invalid_state"
    assert result.message


# ---------------------------------------------------------------- BR-07
def test_lock_window_boundaries():
    assert br.within_lock_window(NOW + hours(23.99), NOW)
    assert not br.within_lock_window(NOW + hours(24), NOW)  # exactly 24 h is allowed
    assert br.within_lock_window(NOW - hours(1), NOW)  # already started


def test_reschedule_blocked_inside_24_hours():
    result = br.can_reschedule("Scheduled", NOW + hours(10), 0, NOW)
    assert not result.ok and result.code == "too_late"


def test_reschedule_allowed_outside_window():
    assert br.can_reschedule("Confirmed", NOW + hours(30), 0, NOW).ok


# ---------------------------------------------------------------- BR-08
def test_reschedule_limit():
    assert br.can_reschedule("Rescheduled", NOW + hours(72), 1, NOW).ok
    result = br.can_reschedule("Rescheduled", NOW + hours(72), 2, NOW)
    assert not result.ok and result.code == "limit_reached"


def test_reschedule_terminal_state_blocked_first():
    result = br.can_reschedule("Cancelled", NOW + hours(5), 5, NOW)
    assert result.code == "invalid_state"


# ---------------------------------------------------------------- BR-11
def test_cancel_allowed_and_late_flag():
    soon = NOW + hours(10)
    assert br.can_cancel("Scheduled", soon, NOW).ok  # allowed, but flagged
    assert br.is_late_cancellation(soon, NOW)
    assert not br.is_late_cancellation(NOW + hours(30), NOW)


def test_cancel_after_start_blocked_and_not_late():
    started = NOW - hours(1)
    assert br.can_cancel("Scheduled", started, NOW).code == "too_late"
    assert not br.is_late_cancellation(started, NOW)


def test_cancel_terminal_blocked():
    assert br.can_cancel("Completed", NOW + hours(48), NOW).code == "invalid_state"


def test_patient_actions_summary():
    actions = br.patient_actions("Scheduled", NOW + hours(10), 0, NOW)
    assert actions["confirm"].ok and actions["cancel"].ok
    assert not actions["reschedule"].ok and actions["reschedule"].code == "too_late"
    done = br.patient_actions("Cancelled", NOW + hours(10), 0, NOW)
    assert not any(r.ok for r in done.values())


# ---------------------------------------------------------------- BR-04 / BR-05
def test_window_starts_at_day_start_and_spans_14_days():
    start, end = br.slot_display_window(NOW + hours(5))
    assert start == datetime(2026, 11, 2, 0, 0, tzinfo=TZ)
    assert end == NOW + hours(5) + timedelta(days=14)


def test_slots_in_window_sorted_and_filtered():
    slots = [
        make_slot("S3", start=NOW + hours(50)),
        make_slot("S1", start=NOW + hours(2)),
        make_slot("S2", start=NOW + hours(2)),  # same start: tie broken by ID
        make_slot("OLD", start=NOW - timedelta(days=2)),  # before today
        make_slot("FAR", start=NOW + timedelta(days=15)),  # beyond window
    ]
    result = br.slots_in_window(slots, NOW)
    assert [s.slot_id for s in result] == ["S1", "S2", "S3"]


def test_earlier_today_slot_is_kept_but_not_selectable():
    earlier = make_slot("E", start=NOW - hours(2))
    assert br.slots_in_window([earlier], NOW) == [earlier]
    assert br.slot_selectability(earlier, NOW) == (False, "past")


@pytest.mark.parametrize(("status", "reason"), [("Booked", "booked"), ("Blocked", "blocked")])
def test_unselectable_reasons(status, reason):
    assert br.slot_selectability(make_slot(status=status), NOW) == (False, reason)


def test_available_future_slot_is_selectable():
    assert br.slot_selectability(make_slot(), NOW) == (True, None)


def test_needs_fallback():
    assert br.needs_fallback(2) and not br.needs_fallback(3)


# ---------------------------------------------------------------- BR-06
def test_validate_new_slot_ok():
    assert br.validate_new_slot(make_slot(), make_appt(), NOW).ok


@pytest.mark.parametrize(
    ("slot_kwargs", "code"),
    [
        ({"status": "Booked"}, "slot_unavailable"),
        ({"status": "Blocked"}, "slot_unavailable"),
        ({"start": NOW - hours(1)}, "slot_past"),
        ({"type_id": "TYP-02"}, "wrong_type"),
        ({"slot_id": "S0"}, "same_slot"),
    ],
)
def test_validate_new_slot_rejections(slot_kwargs, code):
    result = br.validate_new_slot(make_slot(**slot_kwargs), make_appt(), NOW)
    assert not result.ok and result.code == code


def test_validate_new_slot_overlap():
    slot = make_slot(start=NOW + hours(48))
    other = [(NOW + hours(48) + timedelta(minutes=15), NOW + hours(49))]
    assert br.validate_new_slot(slot, make_appt(), NOW, other).code == "overlap"
    touching = [(NOW + hours(48) + timedelta(minutes=30), NOW + hours(49))]
    assert br.validate_new_slot(slot, make_appt(), NOW, touching).ok  # back-to-back is fine


# ---------------------------------------------------------------- BR-12
def test_earliest_by_provider_and_soonest():
    slots = [
        make_slot("A1", "PRV-A", start=NOW + hours(80)),
        make_slot("A2", "PRV-A", start=NOW + hours(30)),
        make_slot("B1", "PRV-B", start=NOW + hours(5)),
        make_slot("B2", "PRV-B", start=NOW + hours(1), status="Booked"),  # ignored
        make_slot("C1", "PRV-C", type_id="TYP-02", start=NOW + hours(2)),  # other type
    ]
    earliest = br.earliest_available_by_provider(slots, NOW, type_id="TYP-01")
    assert earliest == {"PRV-A": NOW + hours(30), "PRV-B": NOW + hours(5)}
    assert br.soonest_provider(earliest) == "PRV-B"


def test_soonest_provider_tie_and_empty():
    tie = {"PRV-B": NOW + hours(3), "PRV-A": NOW + hours(3)}
    assert br.soonest_provider(tie) == "PRV-A"
    assert br.soonest_provider({}) is None


def test_estimate_wait_never_negative():
    assert br.estimate_wait(NOW - hours(1), NOW) == timedelta(0)
    assert br.estimate_wait(NOW + hours(2), NOW) == hours(2)


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (timedelta(seconds=20), "Now"),
        (timedelta(minutes=1), "1 minute"),
        (timedelta(minutes=45), "45 minutes"),
        (hours(1), "1 hour"),
        (hours(5.5), "5 hours"),
        (hours(24), "1 day"),
        (hours(75), "3 days"),
    ],
)
def test_format_wait(delta, text):
    assert br.format_wait(delta) == text


# ---------------------------------------------------------------- BR-13
def test_delay_minutes():
    start = NOW
    assert br.compute_delay_minutes(start, start + timedelta(minutes=20, seconds=40)) == 20
    assert br.compute_delay_minutes(start, start - timedelta(minutes=10)) == 0


def test_is_delayed_threshold_is_strict():
    assert not br.is_delayed(15) and br.is_delayed(16)


# ---------------------------------------------------------------- BR-14
def test_reminder_due_times_48_and_24_hours():
    start = NOW + hours(100)
    assert br.reminder_due_times(start) == [start - hours(48), start - hours(24)]


# ---------------------------------------------------------------- BR-18 / 19 / 24
def test_c1_requires_current_version():
    assert br.c1_satisfied("Granted", "1.0", "1.0")
    assert not br.c1_satisfied("Granted", "0.9", "1.0")  # notice changed: ask again
    assert not br.c1_satisfied("Declined", "1.0", "1.0")
    assert not br.c1_satisfied("Withdrawn", "1.0", "1.0")
    assert not br.c1_satisfied(None, None, "1.0")


def test_reminders_opt_in_and_withdrawal():
    assert br.reminders_enabled("Granted")
    assert not br.reminders_enabled(None)  # default off
    assert not br.reminders_enabled("Withdrawn")
    assert not br.reminders_enabled("Declined")


def test_c3_per_session():
    assert br.c3_satisfied(True) and not br.c3_satisfied(False)


# ---------------------------------------------------------------- BR-15
def test_patient_permissions():
    assert br.is_allowed("Patient", "appointment:read_own")
    assert not br.is_allowed("Patient", "staff:schedule:read")
    assert not br.is_allowed("Patient", "manager:kpis:read")


def test_front_desk_permissions():
    assert br.is_allowed("FrontDesk", "staff:schedule:read")
    assert not br.is_allowed("FrontDesk", "manager:kpis:read")
    assert not br.is_allowed("FrontDesk", "manager:audit:read")
    assert not br.is_allowed("FrontDesk", "appointment:read_own")


def test_manager_permissions():
    for permission in (
        "staff:schedule:read",
        "manager:kpis:read",
        "manager:audit:read",
        "manager:consents:read",
    ):
        assert br.is_allowed("Manager", permission)


def test_deny_by_default():
    assert not br.is_allowed("Patient", "does:not:exist")
    assert not br.is_allowed("Visitor", "appointment:read_own")


# ---------------------------------------------------------------- BR-20
def test_lockout_after_five_failures():
    attempts, locked = 0, None
    for _ in range(4):
        attempts, locked = br.register_failed_attempt(attempts, NOW)
        assert locked is None
    attempts, locked = br.register_failed_attempt(attempts, NOW)
    assert locked == NOW + timedelta(minutes=5)
    assert attempts == 0  # counter resets so the next window starts fresh


def test_is_locked():
    until = NOW + timedelta(minutes=5)
    assert br.is_locked(until, NOW)
    assert not br.is_locked(until, until)  # expires exactly at the deadline
    assert not br.is_locked(None, NOW)


# ---------------------------------------------------------------- dashboard helpers
def test_safe_rate():
    assert br.safe_rate(1, 4) == 0.25 and br.safe_rate(5, 0) == 0.0


@pytest.mark.parametrize(
    ("ratio", "flag"),
    [
        (0.0, "low"),
        (0.59, "low"),
        (0.60, "normal"),
        (0.90, "normal"),
        (0.91, "high"),
        (1.0, "high"),
    ],
)
def test_utilization_flag(ratio, flag):
    assert br.utilization_flag(ratio) == flag


def test_utilization_ratio():
    assert br.utilization_ratio(9, 10) == 0.9 and br.utilization_ratio(0, 0) == 0.0


# ---------------------------------------------------------------- guards
def test_naive_datetimes_rejected():
    naive = datetime(2026, 11, 2, 8, 0)
    with pytest.raises(ValueError):
        br.within_lock_window(naive, NOW)
    with pytest.raises(ValueError):
        br.estimate_wait(NOW, naive)
