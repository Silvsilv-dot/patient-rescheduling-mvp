"""Available slots and estimated waits (BR-04, BR-05, BR-12; US-03)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from rules import business_rules as br
from tests.api.helpers import find_appointments, ready
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.db

DISABLED_REASONS = {"booked", "blocked", "past", "current", "conflict", "unavailable"}


def slots_url(appt_id: str) -> str:
    return f"/api/v1/appointments/{appt_id}/slots"


def setup(client, dataset, **filters):
    appt = find_appointments(dataset, min_hours=48, **filters)[0]
    return appt, ready(client, dataset, appt["appt_id"])


def dataset_slots(dataset):
    return {s["slot_id"]: s for s in dataset.tables["slots"]}


# ------------------------------------------------------------------ slots
def test_at_least_three_slots_in_time_order(client, fresh_db):  # T03-1, T03-2
    appt, headers = setup(client, fresh_db)
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    chosen = [s for s in body["slots"] if s["selectable"]]
    assert len(chosen) >= 3 and body["available_count"] == len(chosen)
    assert body["needs_fallback"] is False and body["message"] is None
    times = [datetime.fromisoformat(s["start_time"]) for s in body["slots"]]
    assert times == sorted(times)


def test_only_same_type_slots_inside_the_14_day_window(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    lookup = dataset_slots(fresh_db)
    window_start, window_end = br.slot_display_window(FIXED_NOW)
    assert body["slots"]
    for slot in body["slots"]:
        source = lookup[slot["slot_id"]]
        assert source["type_id"] == appt["type_id"]
        assert window_start <= source["start_time"] < window_end


def test_unavailable_slots_are_shown_but_not_selectable(client, fresh_db):  # T03-3
    appt, headers = setup(client, fresh_db)
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    lookup = dataset_slots(fresh_db)
    disabled = [s for s in body["slots"] if not s["selectable"]]
    assert disabled
    for slot in disabled:
        assert slot["reason"] in DISABLED_REASONS
        if slot["reason"] in ("booked", "blocked"):
            assert lookup[slot["slot_id"]]["status"].lower() == slot["reason"]
    for slot in body["slots"]:
        if slot["selectable"]:
            assert slot["reason"] is None
            assert lookup[slot["slot_id"]]["status"] == "Available"
            assert lookup[slot["slot_id"]]["start_time"] > FIXED_NOW


def test_patients_own_slot_is_marked_current(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    own = next(s for s in body["slots"] if s["slot_id"] == appt["slot_id"])
    assert own["selectable"] is False and own["reason"] == "current"


def test_slots_that_overlap_the_patients_other_appointments_are_disabled(client, fresh_db):
    by_patient: dict[str, list[dict]] = {}
    for appt in find_appointments(fresh_db, min_hours=48):
        by_patient.setdefault(appt["patient_id"], []).append(appt)
    lookup = dataset_slots(fresh_db)
    for appts in (v for v in by_patient.values() if len(v) >= 2):
        target, other = appts[0], appts[1]
        headers = ready(client, fresh_db, target["appt_id"])
        body = client.get(slots_url(target["appt_id"]), headers=headers).json()
        clash = [
            s
            for s in body["slots"]
            if lookup[s["slot_id"]]["start_time"] == lookup[other["slot_id"]]["start_time"]
            and lookup[s["slot_id"]]["type_id"] == target["type_id"]
            and s["slot_id"] != other["slot_id"]
        ]
        if clash:
            assert all(
                not s["selectable"] and s["reason"] in ("conflict", "booked", "blocked")
                for s in clash
            )
            return
    pytest.fail("no overlapping candidate found in the seeded data")


def test_earlier_slots_today_show_as_past(client, fresh_db, clock):
    clock.now = FIXED_NOW.replace(hour=12, minute=45)
    # Provider D (open from day one) offers this type, so some earlier slots are simply unused.
    appt = find_appointments(fresh_db, min_hours=48, type_id="TYP-03")[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    today = clock.now.date()
    earlier = [
        s
        for s in body["slots"]
        if (t := datetime.fromisoformat(s["start_time"])).date() == today and t < clock.now
    ]
    assert earlier and all(
        not s["selectable"] and s["reason"] in ("past", "booked", "blocked") for s in earlier
    )
    assert any(s["reason"] == "past" for s in earlier)


def test_fallback_when_fewer_than_three_times_are_open(client, fresh_db, db):  # T03-6
    appt, headers = setup(client, fresh_db)
    keep = [
        s["slot_id"]
        for s in client.get(slots_url(appt["appt_id"]), headers=headers).json()["slots"]
        if s["selectable"]
    ][:2]
    db(
        "UPDATE slots SET status = 'Blocked' WHERE type_id = %s AND status = 'Available' "
        "AND NOT (slot_id = ANY(%s))",
        (appt["type_id"], keep),
    )
    body = client.get(slots_url(appt["appt_id"]), headers=headers).json()
    assert body["available_count"] == 2 and body["needs_fallback"] is True
    assert "call the office" in body["message"]


def test_slot_list_reflects_changes_immediately(client, fresh_db, db):  # T03-7
    appt, headers = setup(client, fresh_db)
    first = next(
        s
        for s in client.get(slots_url(appt["appt_id"]), headers=headers).json()["slots"]
        if s["selectable"]
    )
    db("UPDATE slots SET status = 'Booked' WHERE slot_id = %s", (first["slot_id"],))
    after = client.get(slots_url(appt["appt_id"]), headers=headers).json()["slots"]
    changed = next(s for s in after if s["slot_id"] == first["slot_id"])
    assert changed["selectable"] is False and changed["reason"] == "booked"


def test_slot_fields_are_whitelisted(client, fresh_db):
    appt, headers = setup(client, fresh_db)
    for slot in client.get(slots_url(appt["appt_id"]), headers=headers).json()["slots"][:20]:
        assert set(slot) == br.ALLOWED_RESPONSE_FIELDS["patient_slot"]
        assert slot["provider"].startswith("Dr. Demo") and slot["location"].startswith(
            "Demo Clinic"
        )


# ------------------------------------------------------------------ wait times
def expected_earliest(dataset, type_id: str) -> dict[str, datetime]:
    """Independent calculation straight from the seed data."""
    names = {p["provider_id"]: p["display_name"] for p in dataset.tables["providers"]}
    earliest: dict[str, datetime] = {}
    for slot in dataset.tables["slots"]:
        if (
            slot["type_id"] == type_id
            and slot["status"] == "Available"
            and slot["start_time"] > FIXED_NOW
        ):
            name = names[slot["provider_id"]]
            if name not in earliest or slot["start_time"] < earliest[name]:
                earliest[name] = slot["start_time"]
    return earliest


def test_wait_times_for_all_types(client, fresh_db):  # T03-5
    _, headers = setup(client, fresh_db)
    body = client.get("/api/v1/wait-times", headers=headers).json()
    assert "demo data" in body["estimate_note"]
    assert [t["type_id"] for t in body["types"]] == ["TYP-01", "TYP-02", "TYP-03", "TYP-04"]
    for type_block in body["types"]:
        providers = type_block["providers"]
        assert len(providers) == 3
        assert sum(p["is_soonest"] for p in providers) == 1 and providers[0]["is_soonest"]
        starts = [datetime.fromisoformat(p["earliest_start"]) for p in providers]
        assert starts == sorted(starts)
        expected = expected_earliest(fresh_db, type_block["type_id"])
        assert {
            p["provider"]: datetime.fromisoformat(p["earliest_start"]) for p in providers
        } == expected
        for p in providers:
            assert set(p) == br.ALLOWED_RESPONSE_FIELDS["patient_wait_estimate"]
            assert p["wait_label"] == br.format_wait(
                br.estimate_wait(datetime.fromisoformat(p["earliest_start"]), FIXED_NOW)
            )


def test_providers_have_different_waits(client, fresh_db):
    _, headers = setup(client, fresh_db)
    routine = next(
        t
        for t in client.get("/api/v1/wait-times", headers=headers).json()["types"]
        if t["type_id"] == "TYP-01"
    )
    assert len({p["earliest_start"] for p in routine["providers"]}) == 3


def test_wait_times_for_one_type(client, fresh_db):
    _, headers = setup(client, fresh_db)
    body = client.get("/api/v1/wait-times", params={"type_id": "TYP-02"}, headers=headers).json()
    assert [t["type_id"] for t in body["types"]] == ["TYP-02"] and body["types"][0][
        "label"
    ] == "Follow-up Visit"


def test_unknown_or_oversized_type_is_rejected(client, fresh_db):
    _, headers = setup(client, fresh_db)
    assert (
        client.get("/api/v1/wait-times", params={"type_id": "NOPE"}, headers=headers).status_code
        == 404
    )
    assert (
        client.get("/api/v1/wait-times", params={"type_id": "X" * 50}, headers=headers).status_code
        == 422
    )


def test_wait_times_update_when_a_slot_is_taken(client, fresh_db, db):
    _, headers = setup(client, fresh_db)

    def routine_soonest():
        types = client.get(
            "/api/v1/wait-times", params={"type_id": "TYP-01"}, headers=headers
        ).json()
        return types["types"][0]["providers"][0]

    before = routine_soonest()
    db(
        "UPDATE slots SET status = 'Booked' WHERE type_id = 'TYP-01' AND status = 'Available' "
        "AND start_time = %s AND provider_id IN (SELECT provider_id FROM providers "
        "WHERE display_name = %s)",
        (datetime.fromisoformat(before["earliest_start"]), before["provider"]),
    )
    after = routine_soonest()
    assert (
        datetime.fromisoformat(after["earliest_start"])
        > datetime.fromisoformat(before["earliest_start"])
        or after["provider"] != before["provider"]
    )


def test_wait_estimate_is_never_negative_label(client, fresh_db, clock):
    clock.now = FIXED_NOW + timedelta(days=3)
    appt = find_appointments(fresh_db, min_hours=24 * 5)[0]
    headers = ready(client, fresh_db, appt["appt_id"])
    for t in client.get("/api/v1/wait-times", headers=headers).json()["types"]:
        for p in t["providers"]:
            assert datetime.fromisoformat(p["earliest_start"]) > clock.now


# ------------------------------------------------------------------ speed (US-03: under 2 seconds)
def test_slots_and_waits_load_fast(client, fresh_db):  # T03-9
    appt, headers = setup(client, fresh_db)
    timings = []
    for _ in range(20):
        for path in (
            slots_url(appt["appt_id"]),
            "/api/v1/wait-times",
            f"/api/v1/appointments/{appt['appt_id']}",
        ):
            started = time.perf_counter()
            assert client.get(path, headers=headers).status_code == 200
            timings.append(time.perf_counter() - started)
    timings.sort()
    assert timings[int(len(timings) * 0.95) - 1] < 2.0
