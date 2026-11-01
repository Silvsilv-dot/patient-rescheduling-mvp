"""Hostile input is treated as plain data (SQL injection) and oversized input is refused."""

from __future__ import annotations

import pytest

from tests.api.helpers import find_appointments, idem_key, ready, reschedule

pytestmark = pytest.mark.db

ATTACKS = [
    "x'; DROP TABLE slots; --",
    "' OR '1'='1",
    "APT-1' OR 1=1 --",
    "1; DELETE FROM appointments",
    '" OR ""="',
    "%' OR 1=1 --",
    "\\'; TRUNCATE audit_log; --",
]


def table_counts(db) -> dict[str, int]:
    names = (
        "slots",
        "appointments",
        "patients",
        "consents",
        "patient_access",
        "reschedule_history",
    )
    return {n: db(f"SELECT count(*) AS n FROM {n}")[0]["n"] for n in names}


def test_attack_strings_in_sign_in_are_just_wrong_credentials(client, fresh_db, db):
    before = table_counts(db)
    for text in ATTACKS:
        response = client.post("/api/v1/auth/patient", json={"appt_id": text, "access_code": text})
        assert response.status_code == 401, text
        assert response.json()["error"]["code"] == "invalid_credentials"
    assert table_counts(db) == before


def test_attack_strings_as_slot_and_type_ids_change_nothing(client, fresh_db, db):
    appt = find_appointments(fresh_db, min_hours=72, reschedule_count=0)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    before = table_counts(db)
    for text in ATTACKS:
        reply = reschedule(client, headers, appt["appt_id"], text[:32])
        assert reply.status_code == 409 and reply.json()["error"]["code"] == "slot_taken", text
        waits = client.get("/api/v1/wait-times", params={"type_id": text[:16]}, headers=headers)
        assert waits.status_code in (404, 422), text
        path = client.get(f"/api/v1/appointments/{text[:20]}", headers=headers)
        assert path.status_code in (403, 404, 422), text
    assert table_counts(db) == before
    assert (
        db("SELECT status FROM appointments WHERE appt_id = %s", (appt["appt_id"],))[0]["status"]
        == appt["status"]
    )


def test_attack_strings_in_the_idempotency_header_are_refused(client, fresh_db):
    appt = find_appointments(fresh_db, min_hours=72, reschedule_count=0)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    for text in ATTACKS:
        reply = reschedule(client, headers, appt["appt_id"], "SLT-000001", key=text)
        assert reply.status_code == 400, text


def test_attack_strings_in_consent_fields_are_refused(client, fresh_db, db):
    appt = find_appointments(fresh_db, min_hours=72)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    before = db("SELECT count(*) AS n FROM consents")[0]["n"]
    for text in ATTACKS:
        for body in (
            {"consent_type": text, "status": "Granted", "notice_version": "1.0"},
            {"consent_type": "C1", "status": text, "notice_version": "1.0"},
        ):
            assert client.post("/api/v1/consents", json=body, headers=headers).status_code == 422
        stale = client.post(
            "/api/v1/consents",
            headers=headers,
            json={"consent_type": "C1", "status": "Granted", "notice_version": text[:16]},
        )
        assert stale.status_code == 409
    assert db("SELECT count(*) AS n FROM consents")[0]["n"] == before


def test_a_huge_appointment_id_in_the_url_is_refused(client, fresh_db):
    appt = find_appointments(fresh_db, min_hours=72)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    for suffix in ("", "/slots", "/reminders"):
        response = client.get(f"/api/v1/appointments/{'A' * 65}{suffix}", headers=headers)
        assert response.status_code == 422
        assert "AAAA" not in response.text


def test_html_in_text_fields_is_never_reflected_unescaped(client, fresh_db):
    # Responses are JSON only, and no user-supplied text is ever returned in them.
    payload = "<script>alert(1)</script>"
    response = client.post(
        "/api/v1/auth/patient", json={"appt_id": payload, "access_code": payload}
    )
    assert payload not in response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_distinct_keys_are_distinct():  # sanity for the helper used above
    assert idem_key() != idem_key()
