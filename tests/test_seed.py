"""Tests for the synthetic data generator (no database needed)."""

from __future__ import annotations

import re
from datetime import timedelta

from db.seed_synthetic import generate_dataset, summarize
from rules import business_rules as br


def _slot_infos(dataset):
    return [
        br.SlotInfo(
            s["slot_id"],
            s["provider_id"],
            s["type_id"],
            s["start_time"],
            s["end_time"],
            s["status"],
        )
        for s in dataset.tables["slots"]
    ]


def test_minimum_volumes(dataset):
    rows = summarize(dataset)["rows"]
    assert rows["patients"] >= 50
    assert rows["providers"] == 6
    assert rows["locations"] == 3
    assert rows["appointment_types"] == 4
    assert 250 <= rows["appointments"] <= 350  # "about 300"
    days = {s["start_time"].date() for s in dataset.tables["slots"]}
    future_days = {d for d in days if d >= dataset.now.date()}
    assert len(future_days) >= 10  # roughly 14 calendar days of business days


def test_same_seed_and_date_give_identical_data(dataset):
    again = generate_dataset(now=dataset.now, seed=dataset.seed)
    assert again.tables == dataset.tables
    assert again.demo_codes == dataset.demo_codes


def test_first_ten_appointment_ids_are_stable(dataset):
    ids = [a["appt_id"] for a in dataset.tables["appointments"][:10]]
    year = dataset.now.year
    assert ids == [f"APT-{year}{i:04d}" for i in range(1, 11)]


def test_different_seed_changes_data(dataset):
    other = generate_dataset(now=dataset.now, seed=dataset.seed + 1)
    assert other.tables["appointments"] != dataset.tables["appointments"]


def test_referential_integrity(dataset):
    t = dataset.tables
    patients = {p["patient_id"] for p in t["patients"]}
    slots = {s["slot_id"] for s in t["slots"]}
    types = {x["type_id"] for x in t["appointment_types"]}
    providers = {p["provider_id"] for p in t["providers"]}
    locations = {x["location_id"] for x in t["locations"]}
    appts = {a["appt_id"] for a in t["appointments"]}
    assert all(
        s["provider_id"] in providers and s["location_id"] in locations and s["type_id"] in types
        for s in t["slots"]
    )
    assert all(
        a["patient_id"] in patients and a["slot_id"] in slots and a["type_id"] in types
        for a in t["appointments"]
    )
    assert all(r["appt_id"] in appts for r in t["reminders"] + t["patient_access"])
    assert all(
        h["old_slot_id"] in slots and h["new_slot_id"] in slots and h["appt_id"] in appts
        for h in t["reschedule_history"]
    )


def test_appointment_type_matches_its_slot(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    assert all(
        slots[a["slot_id"]]["type_id"] == a["type_id"] for a in dataset.tables["appointments"]
    )


def test_no_slot_has_two_active_appointments(dataset):  # BR-10
    infos = [
        br.AppointmentInfo(a["appt_id"], a["status"], a["type_id"], a["slot_id"], dataset.now)
        for a in dataset.tables["appointments"]
    ]
    assert br.find_double_assigned_slots(infos) == set()


def test_slot_status_matches_appointments(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    held = {a["slot_id"] for a in dataset.tables["appointments"] if a["status"] != "Cancelled"}
    for slot_id, slot in slots.items():
        if slot["status"] == "Booked":
            assert slot_id in held
        if slot["status"] in ("Available", "Blocked"):
            assert slot_id not in held


def test_status_matches_time(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    for appt in dataset.tables["appointments"]:
        is_past = slots[appt["slot_id"]]["start_time"] < dataset.now
        if is_past:
            assert appt["status"] in ("Completed", "No-show", "Cancelled")
        else:
            assert appt["status"] in ("Scheduled", "Confirmed", "Rescheduled", "Cancelled")


def test_delays_only_on_completed(dataset):
    for appt in dataset.tables["appointments"]:
        if appt["status"] != "Completed":
            assert appt["delay_minutes"] == 0
    assert any(
        a["delay_minutes"] > br.DELAY_THRESHOLD_MIN for a in dataset.tables["appointments"]
    )  # dashboard has delays to show


def test_no_patient_double_booked_at_same_time(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    seen = set()
    for appt in dataset.tables["appointments"]:
        key = (appt["patient_id"], slots[appt["slot_id"]]["start_time"])
        assert key not in seen
        seen.add(key)


def test_each_type_has_three_future_slots(dataset):  # US-03
    infos = _slot_infos(dataset)
    for type_row in dataset.tables["appointment_types"]:
        available = [
            s
            for s in br.slots_in_window(infos, dataset.now)
            if s.type_id == type_row["type_id"] and br.slot_selectability(s, dataset.now)[0]
        ]
        assert len(available) >= 3


def test_providers_have_different_waits(dataset):  # US-03 value of the wait comparison
    infos = _slot_infos(dataset)
    earliest = br.earliest_available_by_provider(infos, dataset.now, type_id="TYP-01")
    assert len(earliest) == 3  # providers A, B and E offer Routine Visit
    assert len(set(earliest.values())) == 3
    assert br.soonest_provider(earliest) is not None


def test_reschedule_history_is_consistent(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    appts = {a["appt_id"]: a for a in dataset.tables["appointments"]}
    rescheduled = [a for a in appts.values() if a["status"] == "Rescheduled"]
    assert rescheduled
    history = {h["appt_id"]: h for h in dataset.tables["reschedule_history"]}
    assert set(history) == {a["appt_id"] for a in rescheduled}
    for appt in rescheduled:
        row = history[appt["appt_id"]]
        assert appt["reschedule_count"] == 1
        assert row["new_slot_id"] == appt["slot_id"]
        assert slots[row["old_slot_id"]]["status"] == "Available"
        assert slots[row["old_slot_id"]]["type_id"] == appt["type_id"]


def test_demo_codes_and_reminders(dataset):
    active_future = [
        a["appt_id"]
        for a in dataset.tables["appointments"]
        if a["status"] in ("Scheduled", "Confirmed", "Rescheduled")
    ]
    assert set(dataset.demo_codes) == set(active_future)
    assert all(re.fullmatch(r"\d{6}", c) for c in dataset.demo_codes.values())
    assert {r["appt_id"] for r in dataset.tables["patient_access"]} == set(active_future)
    reminders = dataset.tables["reminders"]
    assert len(reminders) == 2 * len(active_future)  # 48 h and 24 h
    assert all(r["suppressed"] and r["status"] == "Simulated" for r in reminders)


def test_late_cancellation_flag_is_consistent(dataset):
    slots = {s["slot_id"]: s for s in dataset.tables["slots"]}
    for appt in dataset.tables["appointments"]:
        if appt["late_cancellation"]:
            assert appt["status"] == "Cancelled"
        start = slots[appt["slot_id"]]["start_time"]
        if appt["status"] == "Cancelled" and start > dataset.now:
            assert appt["late_cancellation"] == (start - dataset.now < timedelta(hours=24))
