"""Sign-in, lockout, privacy notice and consent (BR-18, BR-19, BR-20, T02-1 to T02-5)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from api.security import decode_token
from services import auth_service
from tests.api.helpers import (
    accept_notice,
    bearer,
    find_appointments,
    ready,
    sign_in,
)
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.db


def first_appt(dataset) -> dict:
    return find_appointments(dataset, min_hours=48)[0]


def attempt(client, appt_id: str, code: str):
    return client.post("/api/v1/auth/patient", json={"appt_id": appt_id, "access_code": code})


# ------------------------------------------------------------------ sign-in
def test_sign_in_success(client, fresh_db):
    appt = first_appt(fresh_db)
    response = attempt(client, appt["appt_id"], fresh_db.demo_codes[appt["appt_id"]])
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["appt_id"] == appt["appt_id"]
    assert set(body) == {"access_token", "token_type", "expires_in", "appt_id"}
    claims = decode_token(body["access_token"], FIXED_NOW)
    assert (claims.role, claims.subject, claims.patient_id) == (
        "Patient",
        appt["appt_id"],
        appt["patient_id"],
    )


def test_wrong_code_and_unknown_id_look_identical(client, fresh_db):
    appt = first_appt(fresh_db)
    wrong = attempt(client, appt["appt_id"], "000000x")
    unknown = attempt(client, "APT-99999999", "123456")
    empty = attempt(client, appt["appt_id"], "")
    assert wrong.status_code == unknown.status_code == empty.status_code == 401
    assert wrong.json() == unknown.json() == empty.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    assert fresh_db.demo_codes[appt["appt_id"]] not in wrong.text


def test_unknown_id_costs_the_same_hashing_work(client, fresh_db, monkeypatch):
    calls = []
    real = auth_service.verify_access_code
    monkeypatch.setattr(
        auth_service, "verify_access_code", lambda c, h: calls.append(1) or real(c, h)
    )
    attempt(client, "APT-99999999", "123456")
    assert len(calls) == 1  # a decoy check, so timing does not reveal which IDs exist
    calls.clear()
    attempt(client, first_appt(fresh_db)["appt_id"], "000000x")
    assert len(calls) == 1


def test_five_wrong_codes_lock_the_sixth_attempt(client, fresh_db, clock, db):  # T02-5
    appt_id = first_appt(fresh_db)["appt_id"]
    codes = [attempt(client, appt_id, "000000x").status_code for _ in range(5)]
    assert codes == [401] * 5  # same generic answer each time
    locked = attempt(client, appt_id, "000000x")
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "locked"
    # Even the correct code is refused while locked.
    assert attempt(client, appt_id, fresh_db.demo_codes[appt_id]).status_code == 429
    row = db(
        "SELECT failed_attempts, locked_until FROM patient_access WHERE appt_id = %s", (appt_id,)
    )[0]
    assert row["failed_attempts"] == 0 and row["locked_until"] == FIXED_NOW + timedelta(minutes=5)

    clock.now = FIXED_NOW + timedelta(minutes=4, seconds=59)
    assert attempt(client, appt_id, fresh_db.demo_codes[appt_id]).status_code == 429
    clock.now = FIXED_NOW + timedelta(minutes=5)
    assert attempt(client, appt_id, fresh_db.demo_codes[appt_id]).status_code == 200


def test_success_resets_the_failure_count(client, fresh_db):
    appt_id = first_appt(fresh_db)["appt_id"]
    for _ in range(4):
        assert attempt(client, appt_id, "000000x").status_code == 401
    assert attempt(client, appt_id, fresh_db.demo_codes[appt_id]).status_code == 200
    for _ in range(4):  # a fresh run of four failures still does not lock
        assert attempt(client, appt_id, "000000x").status_code == 401
    assert attempt(client, appt_id, fresh_db.demo_codes[appt_id]).status_code == 200


def test_lockout_is_per_appointment(client, fresh_db):
    first, second = find_appointments(fresh_db, min_hours=48)[:2]
    for _ in range(5):
        attempt(client, first["appt_id"], "000000x")
    assert (
        attempt(client, second["appt_id"], fresh_db.demo_codes[second["appt_id"]]).status_code
        == 200
    )


def test_failed_and_locked_attempts_are_audited(client, fresh_db, db):
    appt_id = first_appt(fresh_db)["appt_id"]
    for _ in range(5):
        attempt(client, appt_id, "000000x")
    rows = db("SELECT action FROM audit_log WHERE appt_id = %s ORDER BY audit_id", (appt_id,))
    assert [r["action"] for r in rows] == ["auth.failed"] * 5 + ["auth.locked"]
    assert (
        db(
            "SELECT count(*) AS n FROM audit_log WHERE action = 'auth.failed' "
            "AND appt_id = 'APT-99999999'"
        )[0]["n"]
        == 0
    )


def test_rate_limit_per_client(client, fresh_db):
    from api.security import auth_rate_limiter

    for _ in range(auth_rate_limiter.max_calls):
        assert attempt(client, "APT-99999999", "1").status_code == 401
    blocked = attempt(client, "APT-99999999", "1")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"


# ------------------------------------------------------------------ tokens in use
def test_expired_tampered_and_missing_tokens(client, fresh_db, clock):
    appt_id = first_appt(fresh_db)["appt_id"]
    headers = sign_in(client, fresh_db, appt_id)
    url = "/api/v1/consents/me"
    assert client.get(url, headers=headers).status_code == 200
    clock.now = FIXED_NOW + timedelta(minutes=14, seconds=59)
    assert client.get(url, headers=headers).status_code == 200
    clock.now = FIXED_NOW + timedelta(minutes=15)
    expired = client.get(url, headers=headers)
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "unauthorized"
    clock.now = FIXED_NOW
    token = headers["Authorization"].split()[1]
    assert client.get(url, headers=bearer(token[:-4] + "AAAA")).status_code == 401
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer"}).status_code == 401


# ------------------------------------------------------------------ notice
def test_privacy_notice_is_public(client):
    response = client.get("/api/v1/privacy-notice")
    body = response.json()
    assert response.status_code == 200 and body["version"] == "1.0"
    assert "demonstration" in body["text"] and "urgent" in body["text"]
    assert set(body) == {"version", "text", "effective_from"}


# ------------------------------------------------------------------ consent gate (BR-18)
PROTECTED = [
    ("get", "/api/v1/appointments/{a}", None),
    ("post", "/api/v1/appointments/{a}/confirm", None),
    ("post", "/api/v1/appointments/{a}/cancel", {"confirm": True}),
    ("get", "/api/v1/appointments/{a}/slots", None),
    ("get", "/api/v1/appointments/{a}/reminders", None),
    ("post", "/api/v1/appointments/{a}/reschedule", {"new_slot_id": "SLT-000001"}),
    ("get", "/api/v1/wait-times", None),
]


def call(client, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(client, method)(path, **kwargs)


def test_no_data_before_the_notice_is_accepted(client, fresh_db, db):  # T02-1, T02-2
    appt_id = first_appt(fresh_db)["appt_id"]
    headers = sign_in(client, fresh_db, appt_id)
    headers["Idempotency-Key"] = "k" * 20
    for method, path, body in PROTECTED:
        response = call(client, method, path.format(a=appt_id), body, headers)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "consent_required", path
    assert (
        db(
            "SELECT count(*) AS n FROM appointments WHERE appt_id = %s AND status = 'Cancelled'",
            (appt_id,),
        )[0]["n"]
        == 0
    )


def test_declining_keeps_the_data_blocked(client, fresh_db):
    appt_id = first_appt(fresh_db)["appt_id"]
    headers = sign_in(client, fresh_db, appt_id)
    decline = client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C1", "status": "Declined", "notice_version": "1.0"},
    )
    assert decline.status_code == 201 and decline.json()["status"] == "Declined"
    assert client.get(f"/api/v1/appointments/{appt_id}", headers=headers).status_code == 403
    state = client.get("/api/v1/consents/me", headers=headers).json()
    assert state["c1"] == {"status": "Declined", "notice_version": "1.0", "satisfied": False}


def test_accepting_records_version_and_time_and_opens_access(client, fresh_db, db):  # T02-3
    appt = first_appt(fresh_db)
    headers = sign_in(client, fresh_db, appt["appt_id"])
    response = client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C1", "status": "Granted", "notice_version": "1.0"},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["consent_type"], body["status"], body["notice_version"]) == (
        "C1",
        "Granted",
        "1.0",
    )
    rows = db("SELECT subject_id, subject_role, notice_version, status, created_at FROM consents")
    assert len(rows) == 1
    assert rows[0]["subject_id"] == appt["patient_id"] and rows[0]["subject_role"] == "Patient"
    assert rows[0]["created_at"] == FIXED_NOW
    assert client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers).status_code == 200
    assert db("SELECT count(*) AS n FROM audit_log WHERE action = 'consent.grant'")[0]["n"] == 1


def test_withdrawing_c1_blocks_access_again(client, fresh_db):
    appt_id = first_appt(fresh_db)["appt_id"]
    headers = ready(client, fresh_db, appt_id)
    assert client.get(f"/api/v1/appointments/{appt_id}", headers=headers).status_code == 200
    client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C1", "status": "Withdrawn", "notice_version": "1.0"},
    )
    assert client.get(f"/api/v1/appointments/{appt_id}", headers=headers).status_code == 403


def test_outdated_notice_cannot_be_accepted(client, fresh_db, db):
    headers = sign_in(client, fresh_db, first_appt(fresh_db)["appt_id"])
    response = client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C1", "status": "Granted", "notice_version": "0.9"},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "notice_outdated"
    assert db("SELECT count(*) AS n FROM consents")[0]["n"] == 0


def test_a_new_notice_version_requires_consent_again(client, fresh_db, db):  # T07-6 (part)
    appt = first_appt(fresh_db)
    headers = sign_in(client, fresh_db, appt["appt_id"])
    db(
        "INSERT INTO consents (subject_id, subject_role, consent_type, notice_version, status, "
        "created_at) VALUES (%s, 'Patient', 'C1', '0.9', 'Granted', %s)",
        (appt["patient_id"], FIXED_NOW),
    )
    response = client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "consent_required"
    accept_notice(client, headers)
    assert client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers).status_code == 200


def test_consent_applies_to_the_patient_across_appointments(client, fresh_db):
    by_patient: dict[str, list[dict]] = {}
    for appt in find_appointments(fresh_db, min_hours=48):
        by_patient.setdefault(appt["patient_id"], []).append(appt)
    first, second = next(v for v in by_patient.values() if len(v) >= 2)[:2]
    accept_notice(client, sign_in(client, fresh_db, first["appt_id"]))
    other = sign_in(client, fresh_db, second["appt_id"])
    assert client.get(f"/api/v1/appointments/{second['appt_id']}", headers=other).status_code == 200


# ------------------------------------------------------------------ reminder choice (C2)
def test_reminder_choice_is_recorded_and_reported(client, fresh_db):
    headers = ready(client, fresh_db, first_appt(fresh_db)["appt_id"])
    assert client.get("/api/v1/consents/me", headers=headers).json()["c2"]["satisfied"] is False
    for status, expected in (("Granted", True), ("Withdrawn", False), ("Declined", False)):
        response = client.post(
            "/api/v1/consents",
            headers=headers,
            json={"consent_type": "C2", "status": status, "notice_version": "1.0"},
        )
        assert response.status_code == 201
        assert (
            client.get("/api/v1/consents/me", headers=headers).json()["c2"]["satisfied"] is expected
        )


# ------------------------------------------------------------------ consent endpoint safety
def test_consent_endpoints_need_a_patient_token_and_valid_input(client, fresh_db):
    body = {"consent_type": "C1", "status": "Granted", "notice_version": "1.0"}
    assert client.post("/api/v1/consents", json=body).status_code == 401
    assert client.get("/api/v1/consents/me").status_code == 401
    headers = sign_in(client, fresh_db, first_appt(fresh_db)["appt_id"])
    for bad in (
        {**body, "consent_type": "C3"},
        {**body, "status": "Maybe"},
        {**body, "extra": 1},
        {"consent_type": "C1"},
        {**body, "notice_version": ""},
    ):
        assert client.post("/api/v1/consents", json=bad, headers=headers).status_code == 422


def test_missing_notice_is_a_generic_503(client, fresh_db, db):
    db("DELETE FROM notice_versions")
    response = client.get("/api/v1/privacy-notice")
    assert response.status_code == 503 and response.json()["error"]["code"] == "unavailable"


def test_audit_log_can_be_listed_newest_first(client, fresh_db, db):
    from db.connection import get_conn
    from services import audit_service

    appt_id = first_appt(fresh_db)["appt_id"]
    for _ in range(2):
        attempt(client, appt_id, "000000x")
    with get_conn() as conn:
        rows = audit_service.list_recent(conn, limit=5)
    assert rows[0]["audit_id"] > rows[1]["audit_id"] and rows[0]["action"] == "auth.failed"
