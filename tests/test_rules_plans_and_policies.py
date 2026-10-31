"""Tests for the plan, whitelist, audit, header, theme and idempotency rules."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from config.settings import DEMO_BANNER_TEXT, NO_STORE_CACHE_CONTROL, SECURITY_HEADERS
from rules import business_rules as br

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)


def slot(slot_id="NEW", type_id="TYP-01", start=None, status="Available"):
    start = start or NOW + timedelta(hours=60)
    return br.SlotInfo(slot_id, "PRV-A", type_id, start, start + timedelta(minutes=30), status)


def appt(status="Scheduled", start=None, count=0, slot_id="OLD", type_id="TYP-01"):
    start = start or NOW + timedelta(hours=72)
    return br.AppointmentInfo("APT-1", status, type_id, slot_id, start, count)


# ----------------------------------------------------------- BR-09 reschedule plan
def test_reschedule_plan_lists_every_change():
    result, plan = br.plan_reschedule(appt(count=1), slot(), NOW)
    assert result.ok and plan is not None
    assert (plan.appt_id, plan.old_slot_id, plan.new_slot_id) == ("APT-1", "OLD", "NEW")
    assert plan.new_status == "Rescheduled"
    assert plan.new_reschedule_count == 2
    assert plan.old_slot_status == "Available" and plan.new_slot_status == "Booked"
    assert plan.old_start == NOW + timedelta(hours=72)
    assert plan.new_start == NOW + timedelta(hours=60)


@pytest.mark.parametrize(
    ("appointment", "new_slot", "code"),
    [
        (appt(status="Cancelled"), slot(), "invalid_state"),
        (appt(start=NOW + timedelta(hours=5)), slot(), "too_late"),
        (appt(count=2), slot(), "limit_reached"),
        (appt(), slot(status="Booked"), "slot_unavailable"),
        (appt(), slot(type_id="TYP-02"), "wrong_type"),
        (appt(), slot(slot_id="OLD"), "same_slot"),
    ],
)
def test_reschedule_plan_denied_returns_no_plan(appointment, new_slot, code):
    result, plan = br.plan_reschedule(appointment, new_slot, NOW)
    assert not result.ok and result.code == code and plan is None


def test_reschedule_plan_respects_overlap():
    new_slot = slot()
    overlapping = [(new_slot.start_time, new_slot.end_time)]
    result, plan = br.plan_reschedule(appt(), new_slot, NOW, overlapping)
    assert result.code == "overlap" and plan is None


# ----------------------------------------------------------- BR-11 cancel plan
def test_cancel_plan_releases_slot_and_flags_late():
    result, plan = br.plan_cancel(appt(start=NOW + timedelta(hours=10)), NOW)
    assert result.ok and plan.slot_status == "Available" and plan.new_status == "Cancelled"
    assert plan.late_cancellation is True
    _, early = br.plan_cancel(appt(start=NOW + timedelta(hours=40)), NOW)
    assert early.late_cancellation is False


def test_cancel_plan_denied():
    result, plan = br.plan_cancel(appt(status="Completed"), NOW)
    assert result.code == "invalid_state" and plan is None


# ----------------------------------------------------------- BR-10 consistency check
def test_double_assignment_detector():
    a = br.AppointmentInfo("A1", "Scheduled", "T", "S1", NOW)
    b = br.AppointmentInfo("A2", "Confirmed", "T", "S1", NOW)  # same slot, both active
    c = br.AppointmentInfo("A3", "Cancelled", "T", "S1", NOW)  # cancelled does not count
    d = br.AppointmentInfo("A4", "Scheduled", "T", "S2", NOW)
    assert br.find_double_assigned_slots([a, c, d]) == set()
    assert br.find_double_assigned_slots([a, b, c, d]) == {"S1"}


# ----------------------------------------------------------- BR-03 / BR-21 whitelists
def test_patient_appointment_whitelist_blocks_extra_fields():
    data = {
        "appt_id": "APT-1",
        "start_time": "2026-11-05T09:00:00-05:00",
        "time_zone": "America/New_York",
        "location": "Demo Clinic North",
        "label": "Routine Visit",
        "status": "Scheduled",
        "allowed_actions": ["confirm"],
        "patient_id": "PAT-0001",
        "diagnosis": "x",
        "insurance_id": "y",
    }
    assert br.disallowed_fields("patient_appointment", data) == {
        "patient_id",
        "diagnosis",
        "insurance_id",
    }
    cleaned = br.restrict_to_allowed("patient_appointment", data)
    assert set(cleaned) == br.ALLOWED_RESPONSE_FIELDS["patient_appointment"]


def test_unknown_response_name_is_denied():
    with pytest.raises(KeyError):
        br.disallowed_fields("not_a_response", {})


def test_no_whitelist_field_is_prohibited():
    for name, fields in br.ALLOWED_RESPONSE_FIELDS.items():
        assert not any(br.has_prohibited_field_name(f) for f in fields), name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("patient_phone", True),
        ("email", True),
        ("insurance_id", True),
        ("status", False),
        ("start_time", False),
        ("Diagnosis", True),
    ],
)
def test_prohibited_field_names(name, expected):
    assert br.has_prohibited_field_name(name) is expected


# ----------------------------------------------------------- BR-16 audit
def test_audit_entry_valid_and_invalid():
    entry = br.audit_entry("FrontDesk", "appointment.status_update", "APT-1")
    assert (entry.actor_role, entry.action, entry.appt_id) == (
        "FrontDesk",
        "appointment.status_update",
        "APT-1",
    )
    assert br.audit_entry("Patient", "consent.grant").appt_id is None
    with pytest.raises(ValueError):
        br.audit_entry("Visitor", "consent.grant")
    with pytest.raises(ValueError):
        br.audit_entry("Patient", "free text goes here")


# ----------------------------------------------------------- BR-17 / BR-22
def test_demo_banner_detection():
    assert br.demo_banner_present(f"<div>{DEMO_BANNER_TEXT}</div>")
    assert not br.demo_banner_present("<div>Welcome</div>")


def test_security_header_check():
    full = dict(SECURITY_HEADERS)
    assert br.missing_security_headers(full) == []
    assert br.missing_security_headers({k.lower(): v for k, v in full.items()}) == []
    partial = {"X-Content-Type-Options": "nosniff"}
    assert set(br.missing_security_headers(partial)) == {
        "Referrer-Policy",
        "Content-Security-Policy",
    }


def test_appointment_data_needs_no_store():
    headers = dict(SECURITY_HEADERS)
    assert br.missing_security_headers(headers, appointment_data=True) == [
        "Cache-Control: no-store"
    ]
    assert NO_STORE_CACHE_CONTROL == "no-store"
    headers["Cache-Control"] = NO_STORE_CACHE_CONTROL
    assert br.missing_security_headers(headers, appointment_data=True) == []
    headers["Cache-Control"] = "public, max-age=60"
    assert br.missing_security_headers(headers, appointment_data=True) != []


@pytest.mark.parametrize(
    "text",
    [
        "Traceback (most recent call last):",
        'File "/app/api/main.py", line 3',
        "psycopg.OperationalError",
        "postgresql://u:p@host/db",
        "SELECT * FROM slots",
        "password authentication failed",
    ],
)
def test_internal_details_detected(text):
    assert br.contains_internal_details(text)


def test_all_standard_messages_are_safe():
    for code, message in br.MESSAGES.items():
        assert not br.contains_internal_details(message), code


def test_error_body_format():
    assert br.error_body("slot_taken") == {
        "error": {"code": "slot_taken", "message": br.MESSAGES["slot_taken"]}
    }
    assert br.error_body("custom", "Something specific")["error"]["message"] == "Something specific"
    assert br.error_body("unknown_code")["error"]["message"].startswith("Something went wrong")
    with pytest.raises(ValueError):
        br.error_body("x", "Traceback: boom")


def test_consent_and_lockout_messages_exist():
    for code in ("consent_required", "not_found", "locked", "forbidden", "unavailable"):
        assert code in br.MESSAGES


# ----------------------------------------------------------- BR-23 theme and contrast
def test_contrast_known_values():
    assert br.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert br.contrast_ratio("#fff", "#fff") == pytest.approx(1.0)
    assert br.contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)
    assert br.contrast_ratio("#ffffff", "#767676") == br.contrast_ratio("#767676", "#ffffff")


def test_aa_thresholds():
    assert br.meets_wcag_aa("#767676", "#ffffff")  # 4.54 passes text
    assert not br.meets_wcag_aa("#777777", "#ffffff")  # 4.48 fails text
    assert br.meets_wcag_aa("#777777", "#ffffff", kind="large_text")


def test_aa_component_boundary():
    # 3:1 is the minimum for interface components.
    assert br.meets_wcag_aa("#949494", "#ffffff", kind="component")
    assert not br.meets_wcag_aa("#a0a0a0", "#ffffff", kind="component")


@pytest.mark.parametrize("bad", ["", "#12", "#gggggg", "blue", "#12345678"])
def test_invalid_colors_rejected(bad):
    with pytest.raises(ValueError):
        br.relative_luminance(bad)


@pytest.mark.parametrize(
    ("stored", "dark", "expected"),
    [
        ("dark", False, "dark"),
        ("light", True, "light"),
        (None, True, "dark"),
        (None, False, "light"),
        ("purple", True, "dark"),
        ("", False, "light"),
    ],
)
def test_resolve_theme(stored, dark, expected):
    assert br.resolve_theme(stored, dark) == expected


# ----------------------------------------------------------- BR-25 idempotency
@pytest.mark.parametrize(
    ("key", "valid"),
    [
        ("123e4567-e89b-12d3-a456-426614174000", True),
        ("a" * 16, True),
        ("a" * 64, True),
        ("a" * 15, False),
        ("a" * 65, False),
        ("has space in it....", False),
        ("", False),
        (None, False),
        (12345678901234567890, False),
    ],
)
def test_idempotency_key_validation(key, valid):
    assert br.is_valid_idempotency_key(key) is valid


def test_fingerprint_is_stable_and_sensitive():
    a = br.request_fingerprint("reschedule", {"new_slot_id": "S1", "appt": "A"})
    b = br.request_fingerprint("reschedule", {"appt": "A", "new_slot_id": "S1"})  # key order
    assert a == b and len(a) == 64
    assert a != br.request_fingerprint("reschedule", {"new_slot_id": "S2", "appt": "A"})
    assert a != br.request_fingerprint("cancel", {"new_slot_id": "S1", "appt": "A"})


def test_idempotency_outcomes():
    fp = br.request_fingerprint("reschedule", {"new_slot_id": "S1"})
    other = br.request_fingerprint("reschedule", {"new_slot_id": "S2"})
    assert br.idempotency_outcome(None, fp) == "new"
    assert br.idempotency_outcome(fp, fp) == "replay"
    assert br.idempotency_outcome(fp, other) == "conflict"
