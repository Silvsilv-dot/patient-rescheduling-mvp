"""Simulated reminders and opt-in (BR-14, BR-19)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.reminder_service import REMINDER_MESSAGE
from tests.api.helpers import find_appointments, ready, slot_starts

pytestmark = pytest.mark.db


def url(appt_id: str) -> str:
    return f"/api/v1/appointments/{appt_id}/reminders"


def set_c2(client, headers, status: str):
    response = client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C2", "status": status, "notice_version": "1.0"},
    )
    assert response.status_code == 201
    return response


def setup(client, dataset):
    appt = find_appointments(dataset, min_hours=72)[0]
    return appt, ready(client, dataset, appt["appt_id"])


def test_reminders_are_off_by_default(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    assert client.get(url(appt["appt_id"]), headers=headers).json() == {
        "reminders_enabled": False,
        "reminders": [],
    }
    rows = db("SELECT suppressed FROM reminders WHERE appt_id = %s", (appt["appt_id"],))
    assert len(rows) == 2 and all(r["suppressed"] for r in rows)


def test_opting_in_shows_the_48_and_24_hour_reminders(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    set_c2(client, headers, "Granted")
    body = client.get(url(appt["appt_id"]), headers=headers).json()
    start = slot_starts(fresh_db)[appt["slot_id"]]
    assert body["reminders_enabled"] is True
    assert [datetime.fromisoformat(r["due_at"]) for r in body["reminders"]] == [
        start - timedelta(hours=48),
        start - timedelta(hours=24),
    ]
    for reminder in body["reminders"]:
        assert reminder["status"] == "Simulated" and reminder["message"] == REMINDER_MESSAGE
    assert (
        db(
            "SELECT count(*) AS n FROM reminders WHERE appt_id = %s AND suppressed",
            (appt["appt_id"],),
        )[0]["n"]
        == 0
    )


def test_reminder_text_is_generic(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    set_c2(client, headers, "Granted")
    text = client.get(url(appt["appt_id"]), headers=headers).text
    page = client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers).json()
    for private in (page["location"], page["label"], "Dr. Demo", "Demo Clinic", appt["patient_id"]):
        assert private not in text
    assert "never sent" in text


def test_withdrawing_stops_reminders_everywhere_at_once(client, fresh_db, db):  # BR-19
    appt, headers = setup(client, fresh_db)
    set_c2(client, headers, "Granted")
    set_c2(client, headers, "Withdrawn")
    assert client.get(url(appt["appt_id"]), headers=headers).json()["reminders"] == []
    mine = db(
        "SELECT r.suppressed FROM reminders r JOIN appointments a ON a.appt_id = r.appt_id "
        "WHERE a.patient_id = %s",
        (appt["patient_id"],),
    )
    assert mine and all(r["suppressed"] for r in mine)
    actions = [r["action"] for r in db("SELECT action FROM audit_log ORDER BY audit_id")]
    assert "consent.grant" in actions and "consent.withdraw" in actions


def test_declining_is_the_same_as_off(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    set_c2(client, headers, "Declined")
    assert client.get(url(appt["appt_id"]), headers=headers).json()["reminders_enabled"] is False


def test_the_choice_covers_all_of_the_patients_appointments(client, fresh_db, db):
    by_patient: dict[str, list[dict]] = {}
    for a in find_appointments(fresh_db, min_hours=72):
        by_patient.setdefault(a["patient_id"], []).append(a)
    first, second = next(v for v in by_patient.values() if len(v) >= 2)[:2]
    headers_one = ready(client, fresh_db, first["appt_id"])
    set_c2(client, headers_one, "Granted")
    headers_two = ready(client, fresh_db, second["appt_id"])
    assert (
        client.get(url(second["appt_id"]), headers=headers_two).json()["reminders_enabled"] is True
    )
    other_patient = next(
        a
        for a in find_appointments(fresh_db, min_hours=72)
        if a["patient_id"] != first["patient_id"]
    )
    headers_other = ready(client, fresh_db, other_patient["appt_id"])
    assert (
        client.get(url(other_patient["appt_id"]), headers=headers_other).json()["reminders_enabled"]
        is False
    )


def test_a_cancelled_appointment_has_no_reminders(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    set_c2(client, headers, "Granted")
    client.post(
        f"/api/v1/appointments/{appt['appt_id']}/cancel", headers=headers, json={"confirm": True}
    )
    assert client.get(url(appt["appt_id"]), headers=headers).json()["reminders"] == []
