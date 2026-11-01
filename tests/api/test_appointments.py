"""View, confirm, cancel and access control (BR-02, BR-03, BR-11, BR-15, BR-21)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from rules import business_rules as br
from tests.api.helpers import find_appointments, idem_key, ready, sign_in, slot_starts

pytestmark = pytest.mark.db

GENERIC_LABELS = {"Routine Visit", "Follow-up Visit", "Consultation Visit", "Wellness Check"}


def url(appt_id: str, suffix: str = "") -> str:
    return f"/api/v1/appointments/{appt_id}{suffix}"


def pick(dataset, **filters) -> dict:
    return find_appointments(dataset, **filters)[0]


# ------------------------------------------------------------------ view (BR-03, BR-21)
def test_appointment_page_shows_only_permitted_fields(client, fresh_db):  # T02-4
    appt = pick(fresh_db, min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    response = client.get(url(appt["appt_id"]), headers=headers)
    body = response.json()
    assert response.status_code == 200
    assert set(body) == br.ALLOWED_RESPONSE_FIELDS["patient_appointment"]
    assert br.disallowed_fields("patient_appointment", body) == set()
    assert not any(br.has_prohibited_field_name(key) for key in body)
    assert body["appt_id"] == appt["appt_id"] and body["status"] == appt["status"]
    assert body["location"].startswith("Demo Clinic") and body["label"] in GENERIC_LABELS
    assert body["time_zone"] == "America/New_York"
    shown = datetime.fromisoformat(body["start_time"])
    assert shown.utcoffset() is not None
    assert shown == slot_starts(fresh_db)[appt["slot_id"]]  # same instant, office time zone
    for private in (appt["patient_id"], "Demo Patient", "Dr. Demo"):
        assert private not in response.text


def test_actions_for_a_comfortably_future_appointment(client, fresh_db):
    appt = pick(fresh_db, min_hours=48, reschedule_count=0)
    headers = ready(client, fresh_db, appt["appt_id"])
    actions = client.get(url(appt["appt_id"]), headers=headers).json()["allowed_actions"]
    assert set(actions) == {"confirm", "reschedule", "cancel"}
    assert all(a == {"allowed": True, "message": None} for a in actions.values())


def test_inside_24_hours_reschedule_is_blocked_but_cancel_is_not(client, fresh_db):
    appt = pick(fresh_db, max_hours=20)
    headers = ready(client, fresh_db, appt["appt_id"])
    actions = client.get(url(appt["appt_id"]), headers=headers).json()["allowed_actions"]
    assert actions["reschedule"] == {"allowed": False, "message": br.MESSAGES["too_late"]}
    assert actions["cancel"]["allowed"] and actions["confirm"]["allowed"]


def test_responses_are_not_cached_and_carry_security_headers(client, fresh_db):
    appt = pick(fresh_db, min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    response = client.get(url(appt["appt_id"]), headers=headers)
    assert br.missing_security_headers(response.headers, appointment_data=True) == []


# ------------------------------------------------------------------ confirm (BR-02)
def test_confirm(client, fresh_db, db):  # T02-6
    appt = pick(fresh_db, statuses=("Scheduled",), min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    response = client.post(url(appt["appt_id"], "/confirm"), headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "Confirmed"
    assert (
        db("SELECT status FROM appointments WHERE appt_id = %s", (appt["appt_id"],))[0]["status"]
        == "Confirmed"
    )
    audit = db("SELECT actor_role, action FROM audit_log WHERE appt_id = %s", (appt["appt_id"],))
    assert {"actor_role": "Patient", "action": "appointment.confirm"} in audit


def test_confirming_twice_is_harmless(client, fresh_db):
    appt = pick(fresh_db, statuses=("Confirmed",), min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    assert client.post(url(appt["appt_id"], "/confirm"), headers=headers).status_code == 200


# ------------------------------------------------------------------ cancel (BR-11)
def test_cancel_needs_an_explicit_yes(client, fresh_db, db):  # T02-7
    appt = pick(fresh_db, min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    for body in (
        None,
        {},
        {"confirm": False},
        {"confirm": "yes"},
        {"confirm": "true"},
        {"confirm": 1},
        {"confirm": None},
        {"confirm": True, "x": 1},
    ):
        kwargs = {"json": body} if body is not None else {}
        response = client.post(url(appt["appt_id"], "/cancel"), headers=headers, **kwargs)
        assert response.status_code == 422, body
    assert (
        db("SELECT status FROM appointments WHERE appt_id = %s", (appt["appt_id"],))[0]["status"]
        == appt["status"]
    )


def test_cancel_releases_the_slot_and_clears_reminders(client, fresh_db, db):
    appt = pick(fresh_db, min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C2", "status": "Granted", "notice_version": "1.0"},
    )
    assert (
        db("SELECT count(*) AS n FROM reminders WHERE appt_id = %s", (appt["appt_id"],))[0]["n"]
        == 2
    )
    response = client.post(url(appt["appt_id"], "/cancel"), headers=headers, json={"confirm": True})
    assert response.status_code == 200 and response.json()["status"] == "Cancelled"
    assert (
        db("SELECT status FROM slots WHERE slot_id = %s", (appt["slot_id"],))[0]["status"]
        == "Available"
    )
    row = db(
        "SELECT status, late_cancellation FROM appointments WHERE appt_id = %s", (appt["appt_id"],)
    )[0]
    assert row == {"status": "Cancelled", "late_cancellation": False}
    assert (
        db("SELECT count(*) AS n FROM reminders WHERE appt_id = %s", (appt["appt_id"],))[0]["n"]
        == 0
    )
    assert (
        db("SELECT count(*) AS n FROM audit_log WHERE action = 'appointment.cancel'")[0]["n"] == 1
    )


def test_cancelling_inside_24_hours_is_flagged_late(client, fresh_db, db):  # T02-6 (late)
    appt = pick(fresh_db, max_hours=20)
    headers = ready(client, fresh_db, appt["appt_id"])
    assert (
        client.post(
            url(appt["appt_id"], "/cancel"), headers=headers, json={"confirm": True}
        ).status_code
        == 200
    )
    assert (
        db("SELECT late_cancellation FROM appointments WHERE appt_id = %s", (appt["appt_id"],))[0][
            "late_cancellation"
        ]
        is True
    )


def test_after_cancelling_every_action_is_disabled_and_refused(client, fresh_db):  # T02-8
    appt = pick(fresh_db, min_hours=48)
    headers = ready(client, fresh_db, appt["appt_id"])
    client.post(url(appt["appt_id"], "/cancel"), headers=headers, json={"confirm": True})
    shown = client.get(url(appt["appt_id"]), headers=headers).json()
    assert shown["status"] == "Cancelled"
    assert not any(a["allowed"] for a in shown["allowed_actions"].values())
    assert all(
        a["message"] == br.MESSAGES["invalid_state"] for a in shown["allowed_actions"].values()
    )
    for method, suffix, body in (
        ("post", "/confirm", None),
        ("post", "/cancel", {"confirm": True}),
    ):
        kwargs = {"json": body} if body else {}
        refused = getattr(client, method)(url(appt["appt_id"], suffix), headers=headers, **kwargs)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "invalid_state"
    refused = client.post(
        url(appt["appt_id"], "/reschedule"),
        json={"new_slot_id": "SLT-000001"},
        headers={**headers, "Idempotency-Key": idem_key()},
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "invalid_state"


def test_cannot_cancel_after_the_start_time(client, fresh_db, clock):
    appt = pick(fresh_db, min_hours=48)
    start = slot_starts(fresh_db)[appt["slot_id"]]
    clock.now = start + timedelta(hours=1)
    headers = ready(client, fresh_db, appt["appt_id"])  # sign in again at the new time
    response = client.post(url(appt["appt_id"], "/cancel"), headers=headers, json={"confirm": True})
    assert response.status_code == 409 and response.json()["error"]["code"] == "too_late"


# ------------------------------------------------------------------ access control (BR-15)
def test_a_patient_cannot_reach_another_appointment(client, fresh_db, db):  # T07-1
    first, second = find_appointments(fresh_db, min_hours=48)[:2]
    headers = ready(client, fresh_db, first["appt_id"])
    other = second["appt_id"]
    attempts = [
        ("get", url(other), None),
        ("post", url(other, "/confirm"), None),
        ("post", url(other, "/cancel"), {"confirm": True}),
        ("get", url(other, "/slots"), None),
        ("get", url(other, "/reminders"), None),
        ("post", url(other, "/reschedule"), {"new_slot_id": "SLT-000001"}),
    ]
    for method, path, body in attempts:
        kwargs = {"headers": {**headers, "Idempotency-Key": idem_key()}}
        if body:
            kwargs["json"] = body
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"
        assert second["patient_id"] not in response.text and other not in response.text
    assert (
        db("SELECT status FROM appointments WHERE appt_id = %s", (other,))[0]["status"]
        == second["status"]
    )
    denied = db("SELECT actor_role, appt_id FROM audit_log WHERE action = 'access.denied'")
    assert len(denied) == len(attempts)
    assert {d["appt_id"] for d in denied} == {first["appt_id"]}  # the caller's own, not the target


def test_unknown_appointment_looks_the_same_as_someone_elses(client, fresh_db):
    headers = ready(client, fresh_db, pick(fresh_db, min_hours=48)["appt_id"])
    other = client.get(
        url(find_appointments(fresh_db, min_hours=48)[1]["appt_id"]), headers=headers
    )
    unknown = client.get(url("APT-99999999"), headers=headers)
    assert other.status_code == unknown.status_code == 403
    assert other.json() == unknown.json()


def test_no_token_means_401_everywhere(client, fresh_db):
    appt_id = pick(fresh_db, min_hours=48)["appt_id"]
    for method, path, body in (
        ("get", url(appt_id), None),
        ("post", url(appt_id, "/confirm"), None),
        ("post", url(appt_id, "/cancel"), {"confirm": True}),
        ("get", url(appt_id, "/slots"), None),
        ("get", url(appt_id, "/reminders"), None),
        ("get", "/api/v1/wait-times", None),
        ("post", url(appt_id, "/reschedule"), {"new_slot_id": "S"}),
    ):
        kwargs = {"json": body} if body else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


def test_everything_is_scoped_to_the_signed_in_appointment_only(client, fresh_db):
    # A second sign-in for a different appointment must not widen the first token.
    first, second = find_appointments(fresh_db, min_hours=48)[:2]
    token_one = sign_in(client, fresh_db, first["appt_id"])
    sign_in(client, fresh_db, second["appt_id"])
    assert client.get(url(second["appt_id"]), headers=token_one).status_code == 403
