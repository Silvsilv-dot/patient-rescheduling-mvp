"""Rescheduling (BR-06 to BR-10, BR-25; US-04, tests T04-1 to T04-11)."""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from rules import business_rules as br
from services import reminder_service
from tests.api.helpers import (
    find_appointments,
    idem_key,
    ready,
    reschedule,
    selectable_slots,
    slot_starts,
)
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.db


def state(db, appt_id: str) -> dict:
    return db(
        "SELECT slot_id, status, reschedule_count FROM appointments WHERE appt_id = %s", (appt_id,)
    )[0]


def history_rows(db, appt_id: str) -> int:
    return db("SELECT count(*) AS n FROM reschedule_history WHERE appt_id = %s", (appt_id,))[0]["n"]


def slot_status(db, slot_id: str) -> str:
    return db("SELECT status FROM slots WHERE slot_id = %s", (slot_id,))[0]["status"]


def setup(client, dataset, *, min_hours=72, **filters):
    appt = find_appointments(dataset, min_hours=min_hours, reschedule_count=0, **filters)[0]
    headers = ready(client, dataset, appt["appt_id"])
    return appt, headers


def first_target(client, headers, appt_id: str, *, min_hours=72) -> dict:
    return selectable_slots(client, headers, appt_id, min_hours=min_hours)[0]


# ------------------------------------------------------------------ happy path
def test_reschedule_moves_the_appointment(client, fresh_db, db):  # T04-1, T04-2
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    response = reschedule(client, headers, appt["appt_id"], target["slot_id"])
    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["reschedule_count"]) == ("Rescheduled", 1)
    starts = slot_starts(fresh_db)
    assert datetime.fromisoformat(body["old_start_time"]) == starts[appt["slot_id"]]
    assert datetime.fromisoformat(body["new_start_time"]) == starts[target["slot_id"]]

    assert state(db, appt["appt_id"]) == {
        "slot_id": target["slot_id"],
        "status": "Rescheduled",
        "reschedule_count": 1,
    }
    assert slot_status(db, target["slot_id"]) == "Booked"
    assert slot_status(db, appt["slot_id"]) == "Available"  # old slot released
    history = db("SELECT * FROM reschedule_history WHERE appt_id = %s", (appt["appt_id"],))
    assert len(history) == 1
    assert (history[0]["old_slot_id"], history[0]["new_slot_id"]) == (
        appt["slot_id"],
        target["slot_id"],
    )
    assert history[0]["old_start"] == starts[appt["slot_id"]]
    assert history[0]["new_start"] == starts[target["slot_id"]]
    assert (
        db("SELECT count(*) AS n FROM audit_log WHERE action = 'appointment.reschedule'")[0]["n"]
        == 1
    )

    page = client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers).json()
    assert page["status"] == "Rescheduled"
    assert datetime.fromisoformat(page["start_time"]) == starts[target["slot_id"]]
    assert page["allowed_actions"]["reschedule"]["allowed"] is True  # one change left


def test_another_provider_of_the_same_type_is_allowed(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    current = next(
        s
        for s in client.get(
            f"/api/v1/appointments/{appt['appt_id']}/slots", headers=headers
        ).json()["slots"]
        if s["slot_id"] == appt["slot_id"]
    )["provider"]
    other = next(
        s
        for s in selectable_slots(client, headers, appt["appt_id"], min_hours=72)
        if s["provider"] != current
    )
    assert reschedule(client, headers, appt["appt_id"], other["slot_id"]).status_code == 200


def test_the_old_slot_goes_back_to_the_pool_and_can_be_taken(client, fresh_db, db):
    appts = find_appointments(fresh_db, min_hours=72, reschedule_count=0)
    first = appts[0]
    headers_one = ready(client, fresh_db, first["appt_id"])
    target = first_target(client, headers_one, first["appt_id"])
    assert reschedule(client, headers_one, first["appt_id"], target["slot_id"]).status_code == 200

    for second in (a for a in appts[1:] if a["type_id"] == first["type_id"]):
        headers_two = ready(client, fresh_db, second["appt_id"])
        offered = {s["slot_id"] for s in selectable_slots(client, headers_two, second["appt_id"])}
        if first["slot_id"] in offered:
            assert (
                reschedule(client, headers_two, second["appt_id"], first["slot_id"]).status_code
                == 200
            )
            assert state(db, second["appt_id"])["slot_id"] == first["slot_id"]
            active = db(
                "SELECT count(*) AS n FROM appointments WHERE slot_id = %s AND status IN "
                "('Scheduled','Confirmed','Rescheduled')",
                (first["slot_id"],),
            )[0]["n"]
            assert active == 1
            return
    pytest.fail("no second patient could use the released slot")


def test_reminders_follow_the_new_time(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    client.post(
        "/api/v1/consents",
        headers=headers,
        json={"consent_type": "C2", "status": "Granted", "notice_version": "1.0"},
    )
    target = first_target(client, headers, appt["appt_id"])
    assert reschedule(client, headers, appt["appt_id"], target["slot_id"]).status_code == 200
    new_start = slot_starts(fresh_db)[target["slot_id"]]
    rows = db(
        "SELECT due_at, suppressed FROM reminders WHERE appt_id = %s ORDER BY due_at",
        (appt["appt_id"],),
    )
    assert [r["due_at"] for r in rows] == [
        new_start - timedelta(hours=48),
        new_start - timedelta(hours=24),
    ]
    assert not any(r["suppressed"] for r in rows)


def test_without_opt_in_new_reminders_stay_suppressed(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    reschedule(client, headers, appt["appt_id"], target["slot_id"])
    rows = db("SELECT suppressed FROM reminders WHERE appt_id = %s", (appt["appt_id"],))
    assert len(rows) == 2 and all(r["suppressed"] for r in rows)


# ------------------------------------------------------------------ refusals (BR-06 to BR-08)
def refused(client, headers, appt_id, slot_id, code, db, before):
    history_before = history_rows(db, appt_id)
    response = reschedule(client, headers, appt_id, slot_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    assert state(db, appt_id) == before  # nothing changed
    assert history_rows(db, appt_id) == history_before


def dataset_slot(dataset, **conditions):
    return next(s for s in dataset.tables["slots"] if all(s[k] == v for k, v in conditions.items()))


def test_a_taken_slot_is_refused(client, fresh_db, db):  # T04-4
    appt, headers = setup(client, fresh_db)
    booked = next(
        s
        for s in fresh_db.tables["slots"]
        if s["status"] == "Booked"
        and s["type_id"] == appt["type_id"]
        and s["slot_id"] != appt["slot_id"]  # someone else's booking, not the patient's own
        and s["start_time"] > FIXED_NOW + timedelta(days=3)
    )
    refused(
        client,
        headers,
        appt["appt_id"],
        booked["slot_id"],
        "slot_taken",
        db,
        state(db, appt["appt_id"]),
    )


def test_a_blocked_slot_and_an_unknown_slot_are_refused(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    before = state(db, appt["appt_id"])
    blocked = next(
        s
        for s in fresh_db.tables["slots"]
        if s["status"] == "Blocked"
        and s["type_id"] == appt["type_id"]
        and s["start_time"] > FIXED_NOW + timedelta(days=3)
    )
    refused(client, headers, appt["appt_id"], blocked["slot_id"], "slot_taken", db, before)
    refused(client, headers, appt["appt_id"], "SLT-999999", "slot_taken", db, before)


def test_a_past_slot_is_refused(client, fresh_db, db):  # T04-5
    appt, headers = setup(client, fresh_db)
    past = next(
        s
        for s in fresh_db.tables["slots"]
        if s["status"] == "Available"
        and s["type_id"] == appt["type_id"]
        and s["start_time"] < FIXED_NOW
    )
    refused(
        client,
        headers,
        appt["appt_id"],
        past["slot_id"],
        "slot_past",
        db,
        state(db, appt["appt_id"]),
    )
    assert slot_status(db, past["slot_id"]) == "Available"


def test_a_slot_for_a_different_type_is_refused(client, fresh_db, db):  # T04-6
    appt, headers = setup(client, fresh_db)
    other = next(
        s
        for s in fresh_db.tables["slots"]
        if s["status"] == "Available"
        and s["type_id"] != appt["type_id"]
        and s["start_time"] > FIXED_NOW + timedelta(days=3)
    )
    refused(
        client,
        headers,
        appt["appt_id"],
        other["slot_id"],
        "wrong_type",
        db,
        state(db, appt["appt_id"]),
    )
    assert slot_status(db, other["slot_id"]) == "Available"


def test_choosing_the_current_time_is_refused(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    refused(
        client,
        headers,
        appt["appt_id"],
        appt["slot_id"],
        "same_slot",
        db,
        state(db, appt["appt_id"]),
    )


def test_an_overlapping_time_is_refused(client, fresh_db, db):
    by_patient: dict[str, list[dict]] = {}
    for a in find_appointments(fresh_db, min_hours=72, reschedule_count=0):
        by_patient.setdefault(a["patient_id"], []).append(a)
    for appts in (v for v in by_patient.values() if len(v) >= 2):
        for appt in appts:
            headers = ready(client, fresh_db, appt["appt_id"])
            body = client.get(
                f"/api/v1/appointments/{appt['appt_id']}/slots", headers=headers
            ).json()
            clash = [s for s in body["slots"] if s["reason"] == "conflict"]
            if clash:
                refused(
                    client,
                    headers,
                    appt["appt_id"],
                    clash[0]["slot_id"],
                    "overlap",
                    db,
                    state(db, appt["appt_id"]),
                )
                assert slot_status(db, clash[0]["slot_id"]) == "Available"
                return
    pytest.fail("no overlapping candidate found in the seeded data")


def test_inside_24_hours_it_is_refused(client, fresh_db, db):  # T04-7
    appt = find_appointments(fresh_db, max_hours=20)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    target = first_target(client, headers, appt["appt_id"], min_hours=72)
    refused(
        client,
        headers,
        appt["appt_id"],
        target["slot_id"],
        "too_late",
        db,
        state(db, appt["appt_id"]),
    )
    assert slot_status(db, target["slot_id"]) == "Available"


def test_a_third_change_is_refused(client, fresh_db, db):  # T04-8
    appt, headers = setup(client, fresh_db)
    for expected in (1, 2):
        target = first_target(client, headers, appt["appt_id"])
        response = reschedule(client, headers, appt["appt_id"], target["slot_id"])
        assert response.status_code == 200 and response.json()["reschedule_count"] == expected
    third = first_target(client, headers, appt["appt_id"])
    refused_state = state(db, appt["appt_id"])
    response = reschedule(client, headers, appt["appt_id"], third["slot_id"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "limit_reached"
    assert state(db, appt["appt_id"]) == refused_state
    assert (
        db("SELECT count(*) AS n FROM reschedule_history WHERE appt_id = %s", (appt["appt_id"],))[
            0
        ]["n"]
        == 2
    )
    shown = client.get(f"/api/v1/appointments/{appt['appt_id']}", headers=headers).json()
    assert shown["allowed_actions"]["reschedule"] == {
        "allowed": False,
        "message": br.MESSAGES["limit_reached"],
    }


# ------------------------------------------------------------------ idempotency (BR-25)
def test_a_retry_with_the_same_key_applies_once(client, fresh_db, db):  # T04-10
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    key = idem_key()
    first = reschedule(client, headers, appt["appt_id"], target["slot_id"], key)
    second = reschedule(client, headers, appt["appt_id"], target["slot_id"], key)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert (
        "idempotent-replay" not in first.headers and second.headers["idempotent-replay"] == "true"
    )
    assert state(db, appt["appt_id"])["reschedule_count"] == 1
    assert history_rows(db, appt["appt_id"]) == 1
    assert (
        db("SELECT count(*) AS n FROM audit_log WHERE action = 'appointment.reschedule'")[0]["n"]
        == 1
    )
    assert db("SELECT count(*) AS n FROM idempotency_keys")[0]["n"] == 1


def test_reusing_a_key_for_a_different_request_is_refused(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    targets = selectable_slots(client, headers, appt["appt_id"], min_hours=72)
    key = idem_key()
    assert (
        reschedule(client, headers, appt["appt_id"], targets[0]["slot_id"], key).status_code == 200
    )
    clash = reschedule(client, headers, appt["appt_id"], targets[1]["slot_id"], key)
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "idempotency_conflict"
    assert state(db, appt["appt_id"])["slot_id"] == targets[0]["slot_id"]
    assert slot_status(db, targets[1]["slot_id"]) == "Available"


def test_the_key_header_is_required_and_checked(client, fresh_db, db):
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    path = f"/api/v1/appointments/{appt['appt_id']}/reschedule"
    for bad in (None, "", "short", "has spaces in the key!!", "k" * 65):
        extra = {"Idempotency-Key": bad} if bad is not None else {}
        response = client.post(
            path, json={"new_slot_id": target["slot_id"]}, headers={**headers, **extra}
        )
        assert (
            response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"
        ), bad
    assert slot_status(db, target["slot_id"]) == "Available"


def test_the_same_key_for_a_different_appointment_is_independent(client, fresh_db, db):
    first, second = find_appointments(fresh_db, min_hours=72, reschedule_count=0)[:2]
    key = idem_key()
    for appt in (first, second):
        headers = ready(client, fresh_db, appt["appt_id"])
        target = first_target(client, headers, appt["appt_id"], min_hours=96)
        response = reschedule(client, headers, appt["appt_id"], target["slot_id"], key)
        assert response.status_code == 200 and "idempotent-replay" not in response.headers


# ------------------------------------------------------------------ never assigned twice (BR-10)
def common_targets(client, group, dataset, min_hours=96) -> tuple[list[dict], list[str]]:
    """Sign in a group of appointments and find slots that every one of them may choose."""
    sessions = [(a, ready(client, dataset, a["appt_id"])) for a in group]
    common = None
    for appt, headers in sessions:
        ids = [
            s["slot_id"]
            for s in selectable_slots(client, headers, appt["appt_id"], min_hours=min_hours)
        ]
        common = ids if common is None else [i for i in common if i in set(ids)]
    return sessions, common or []


def race(app, sessions, slot_id: str) -> list:
    barrier = threading.Barrier(len(sessions))
    results: list = [None] * len(sessions)

    def worker(index: int, appt: dict, headers: dict) -> None:
        local = TestClient(app)  # its own client per thread
        barrier.wait()
        results[index] = reschedule(local, headers, appt["appt_id"], slot_id)

    threads = [threading.Thread(target=worker, args=(i, a, h)) for i, (a, h) in enumerate(sessions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(r is not None for r in results), "a request did not finish"
    return results


def biggest_group(dataset, size: int) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    seen_patients: dict[str, set] = {}
    for a in find_appointments(dataset, min_hours=96, reschedule_count=0):
        if a["patient_id"] in seen_patients.setdefault(a["type_id"], set()):
            continue  # one appointment per patient so patients cannot block themselves
        seen_patients[a["type_id"]].add(a["patient_id"])
        by_type.setdefault(a["type_id"], []).append(a)
    group = max(by_type.values(), key=len)
    assert len(group) >= size
    return group[:size]


def test_six_patients_race_for_one_slot_and_exactly_one_wins(client, app, fresh_db, db):  # T04-3
    group = biggest_group(fresh_db, 6)
    sessions, common = common_targets(client, group, fresh_db)
    assert common, "need a slot all six may choose"
    target = common[0]
    results = race(app, sessions, target)

    codes = Counter(r.status_code for r in results)
    assert codes == {200: 1, 409: 5}, [r.text for r in results]
    assert all(r.json()["error"]["code"] == "slot_taken" for r in results if r.status_code == 409)
    assert slot_status(db, target) == "Booked"
    holders = db(
        "SELECT appt_id FROM appointments WHERE slot_id = %s AND status IN "
        "('Scheduled','Confirmed','Rescheduled')",
        (target,),
    )
    assert len(holders) == 1
    winner = next(
        a for a, r in zip((s[0] for s in sessions), results, strict=True) if r.status_code == 200
    )
    assert holders[0]["appt_id"] == winner["appt_id"]
    for appt, _ in sessions:  # losers are untouched
        if appt["appt_id"] != winner["appt_id"]:
            assert state(db, appt["appt_id"]) == {
                "slot_id": appt["slot_id"],
                "status": appt["status"],
                "reschedule_count": 0,
            }
    assert (
        db("SELECT count(*) AS n FROM reschedule_history WHERE new_slot_id = %s", (target,))[0]["n"]
        == 1
    )


def test_repeated_races_never_double_book(client, app, fresh_db, db):  # T04-3, repeated
    pool = biggest_group(fresh_db, 12)
    for round_number in range(4):
        group = pool[round_number * 3 : (round_number + 1) * 3]
        sessions, common = common_targets(client, group, fresh_db)
        assert common
        target = common[round_number % len(common)]
        results = race(app, sessions, target)
        assert Counter(r.status_code for r in results) == {200: 1, 409: 2}
        holders = db(
            "SELECT count(*) AS n FROM appointments WHERE slot_id = %s AND status IN "
            "('Scheduled','Confirmed','Rescheduled')",
            (target,),
        )[0]["n"]
        assert holders == 1
    over_booked = db(
        "SELECT slot_id FROM appointments WHERE status IN ('Scheduled','Confirmed',"
        "'Rescheduled') GROUP BY slot_id HAVING count(*) > 1"
    )
    assert over_booked == []


# ------------------------------------------------------------------ all or nothing (BR-09)
def test_a_failure_part_way_leaves_nothing_changed(client, fresh_db, db, monkeypatch):  # T04-9
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    before = state(db, appt["appt_id"])
    audit_before = db("SELECT count(*) AS n FROM audit_log")[0]["n"]
    history_before = history_rows(db, appt["appt_id"])

    def explode(*args, **kwargs):
        raise RuntimeError("simulated failure after the new slot was claimed")

    monkeypatch.setattr(reminder_service, "regenerate", explode)
    response = reschedule(client, headers, appt["appt_id"], target["slot_id"])
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal"
    assert not br.contains_internal_details(response.text)
    monkeypatch.undo()

    assert state(db, appt["appt_id"]) == before
    assert slot_status(db, target["slot_id"]) == "Available"  # the claim was rolled back
    assert slot_status(db, appt["slot_id"]) == "Booked"
    assert history_rows(db, appt["appt_id"]) == history_before
    assert db("SELECT count(*) AS n FROM idempotency_keys")[0]["n"] == 0
    assert db("SELECT count(*) AS n FROM audit_log")[0]["n"] == audit_before
    assert reschedule(client, headers, appt["appt_id"], target["slot_id"]).status_code == 200


def test_replay_still_returns_the_original_result_after_a_later_cancel(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    target = first_target(client, headers, appt["appt_id"])
    key = idem_key()
    original = reschedule(client, headers, appt["appt_id"], target["slot_id"], key).json()
    client.post(
        f"/api/v1/appointments/{appt['appt_id']}/cancel", headers=headers, json={"confirm": True}
    )
    replay = reschedule(client, headers, appt["appt_id"], target["slot_id"], key)
    assert replay.status_code == 200 and replay.json() == original


# ------------------------------------------------------------------ speed (US-04: under 2 seconds)
def test_reschedule_is_fast(client, fresh_db):  # T04-11
    timings = []
    for appt in find_appointments(fresh_db, min_hours=72, reschedule_count=0)[:8]:
        headers = ready(client, fresh_db, appt["appt_id"])
        target = first_target(client, headers, appt["appt_id"])
        started = time.perf_counter()
        assert reschedule(client, headers, appt["appt_id"], target["slot_id"]).status_code == 200
        timings.append(time.perf_counter() - started)
    assert max(timings) < 2.0


def test_the_database_itself_refuses_a_double_assignment(client, fresh_db, db):  # BR-10 backstop
    """Even if a slot wrongly looks free, a second active appointment cannot be attached to it."""
    first, second = find_appointments(fresh_db, min_hours=72, reschedule_count=0)[:2]
    headers = ready(client, fresh_db, first["appt_id"])
    target = first_target(client, headers, first["appt_id"])
    # Corrupt the data on purpose: another active appointment holds the slot, yet it says Available.
    db(
        "UPDATE appointments SET slot_id = %s WHERE appt_id = %s",
        (target["slot_id"], second["appt_id"]),
    )
    before = state(db, first["appt_id"])
    history_before = history_rows(db, first["appt_id"])

    response = reschedule(client, headers, first["appt_id"], target["slot_id"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "slot_taken"
    assert state(db, first["appt_id"]) == before
    assert history_rows(db, first["appt_id"]) == history_before
    assert slot_status(db, target["slot_id"]) == "Available"  # the claim was rolled back too
    assert slot_status(db, first["slot_id"]) == "Booked"
