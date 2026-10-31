"""Synthetic data generator and loader for the demo database.

Everything produced here is made up: names, IDs, locations, dates and labels.
Generation is a pure function of ``(seed, now)``, so the same inputs always
give the same data (BR-17, US-01 criterion 2). Dates are relative to ``now`` so
the demo never looks stale.

Usage (from the project root)::

    python -m db.seed_synthetic --reset
    python -m db.seed_synthetic --reset --seed 7 --credentials 15

Without ``--reset`` the script refuses to touch a database that already holds
data. ``--reset`` wipes every table in the target database, so point it only at
a demo or test database.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from config.settings import NOTICE_VERSION, OFFICE_TZ
from rules.business_rules import (
    ACTIVE_STATUSES,
    AppointmentStatus,
    is_late_cancellation,
    reminder_due_times,
)

DEFAULT_SEED = 2026
N_PATIENTS = 60
PAST_DAYS = 7
FUTURE_DAYS = 14

PAST_FILL = 0.25  # share of past slots that were booked
FAR_FILL = 0.10  # share of later future slots that are booked
BLOCKED_PROB = 0.03  # share of slots blocked (for example, provider unavailable)

LOCATIONS = [
    ("LOC-01", "Demo Clinic North"),
    ("LOC-02", "Demo Clinic South"),
    ("LOC-03", "Demo Clinic East"),
]

# (type_id, generic label, minutes). All slots are 30 minutes in this demo.
APPOINTMENT_TYPES = [
    ("TYP-01", "Routine Visit", 30),
    ("TYP-02", "Follow-up Visit", 30),
    ("TYP-03", "Consultation Visit", 30),
    ("TYP-04", "Wellness Check", 30),
]

# (provider_id, name, location, offered types, busy_days, busy_fill)
# The first `busy_days` business days of each provider are filled to
# `busy_fill`, which gives each provider a different estimated wait.
PROVIDERS = [
    ("PRV-A", "Dr. Demo A", "LOC-01", ("TYP-01", "TYP-02"), 2, 1.0),
    ("PRV-B", "Dr. Demo B", "LOC-01", ("TYP-01", "TYP-03"), 1, 0.6),
    ("PRV-C", "Dr. Demo C", "LOC-02", ("TYP-02", "TYP-04"), 3, 1.0),
    ("PRV-D", "Dr. Demo D", "LOC-02", ("TYP-03", "TYP-04"), 0, 0.0),
    ("PRV-E", "Dr. Demo E", "LOC-03", ("TYP-01", "TYP-04"), 1, 1.0),
    ("PRV-F", "Dr. Demo F", "LOC-03", ("TYP-02", "TYP-03"), 2, 1.0),
]

SLOT_TIMES = [time(h, m) for h in (9, 10, 11) for m in (0, 30)] + [
    time(h, m) for h in (13, 14, 15, 16) for m in (0, 30)
]  # 14 slots per provider per business day (lunch 12:00-13:00 has none)
SLOT_MINUTES = 30

DEMO_NOTICE_TEXT = (
    "This is a demonstration. All names, appointments and dates are made up and no real "
    "patient information is used. This demo does not give medical advice. If you need "
    "urgent care, contact your office or emergency services. Reminders are simulated and "
    "never sent. You can turn reminder preferences on or off at any time. Demo data is "
    "reset regularly. Select Accept to view the demo appointment."
)

TABLE_ORDER = [
    "locations",
    "providers",
    "appointment_types",
    "patients",
    "slots",
    "appointments",
    "patient_access",
    "reschedule_history",
    "reminders",
    "notice_versions",
]
ALL_TABLES = TABLE_ORDER + ["consents", "idempotency_keys", "audit_log"]


@dataclass
class Dataset:
    """Generated rows by table, plus the plain-text demo access codes."""

    now: datetime
    seed: int
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    demo_codes: dict[str, str] = field(default_factory=dict)


def default_seed_now() -> datetime:
    """Today at 08:00 in the office time zone (stable for the whole day)."""
    tz = ZoneInfo(OFFICE_TZ)
    return datetime.combine(datetime.now(tz).date(), time(8, 0), tzinfo=tz)


def _business_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def generate_dataset(now: Optional[datetime] = None, seed: int = DEFAULT_SEED) -> Dataset:
    """Build the full synthetic dataset. Pure: no database or clock access."""
    now = now or default_seed_now()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    tz = ZoneInfo(OFFICE_TZ)
    now = now.astimezone(tz)  # slot dates and DST are always handled in office time
    rng = random.Random(seed)
    today = now.date()

    patients = [
        {"patient_id": f"PAT-{i:04d}", "display_name": f"Demo Patient {i:02d}"}
        for i in range(1, N_PATIENTS + 1)
    ]
    patient_ids = [p["patient_id"] for p in patients]
    busy_starts: dict[str, set[datetime]] = {pid: set() for pid in patient_ids}

    slots: list[dict[str, Any]] = []
    appointments: list[dict[str, Any]] = []
    slot_by_id: dict[str, dict[str, Any]] = {}
    slot_future_index: dict[str, Optional[int]] = {}
    slot_busy_days: dict[str, int] = {}

    past_days = _business_days(today - timedelta(days=PAST_DAYS), today - timedelta(days=1))
    future_days = _business_days(today, today + timedelta(days=FUTURE_DAYS))
    day_plan = [(d, None) for d in past_days] + [(d, i) for i, d in enumerate(future_days)]

    slot_seq = 0
    appt_seq = 0

    for provider_id, _name, location_id, type_ids, busy_days, busy_fill in PROVIDERS:
        for day, future_index in day_plan:
            for slot_time in SLOT_TIMES:
                slot_seq += 1
                start = datetime.combine(day, slot_time, tzinfo=tz)
                slot = {
                    "slot_id": f"SLT-{slot_seq:06d}",
                    "provider_id": provider_id,
                    "location_id": location_id,
                    "type_id": rng.choice(type_ids),
                    "start_time": start,
                    "end_time": start + timedelta(minutes=SLOT_MINUTES),
                    "status": "Available",
                }
                slots.append(slot)
                slot_by_id[slot["slot_id"]] = slot
                slot_future_index[slot["slot_id"]] = future_index
                slot_busy_days[slot["slot_id"]] = busy_days

                roll_block = rng.random()
                roll_book = rng.random()
                if roll_block < BLOCKED_PROB:
                    slot["status"] = "Blocked"
                    continue

                is_past = start < now
                if is_past:
                    fill = PAST_FILL
                elif future_index is not None and future_index < busy_days:
                    fill = busy_fill
                else:
                    fill = FAR_FILL
                if roll_book >= fill:
                    continue

                # ---- create an appointment on this slot -------------------
                appt_seq += 1
                patient_id = _pick_patient(rng, patient_ids, busy_starts, start)
                status, delay, late = _pick_status(
                    rng, is_past, future_index, busy_days, start, now
                )
                created_at = (
                    start - timedelta(days=rng.randint(3, 14))
                    if is_past
                    else now - timedelta(days=rng.randint(1, 10))
                )
                appointments.append(
                    {
                        "appt_id": f"APT-{now.year}{appt_seq:04d}",
                        "patient_id": patient_id,
                        "slot_id": slot["slot_id"],
                        "type_id": slot["type_id"],
                        "status": status,
                        "delay_minutes": delay,
                        "reschedule_count": 0,
                        "late_cancellation": late,
                        "created_at": created_at,
                        "updated_at": created_at,
                    }
                )
                # A cancelled appointment releases its slot (BR-11).
                slot["status"] = "Available" if status == "Cancelled" else "Booked"

    history = _assign_reschedules(
        rng, now, appointments, slots, slot_by_id, slot_future_index, slot_busy_days
    )

    demo_codes: dict[str, str] = {}
    reminders: list[dict[str, Any]] = []
    for appt in appointments:
        start = slot_by_id[appt["slot_id"]]["start_time"]
        if AppointmentStatus(appt["status"]) in ACTIVE_STATUSES and start > now:
            demo_codes[appt["appt_id"]] = f"{rng.randint(0, 999999):06d}"
            for due in reminder_due_times(start):
                reminders.append(
                    {
                        "appt_id": appt["appt_id"],
                        "due_at": due,
                        "status": "Simulated",
                        "suppressed": True,
                    }
                )

    tables: dict[str, list[dict[str, Any]]] = {
        "locations": [{"location_id": i, "name": n} for i, n in LOCATIONS],
        "providers": [
            {"provider_id": pid, "display_name": name, "location_id": loc}
            for pid, name, loc, _types, _bd, _bf in PROVIDERS
        ],
        "appointment_types": [
            {"type_id": i, "label": label, "duration_min": minutes}
            for i, label, minutes in APPOINTMENT_TYPES
        ],
        "patients": patients,
        "slots": slots,
        "appointments": appointments,
        "patient_access": [{"appt_id": a} for a in demo_codes],
        "reschedule_history": history,
        "reminders": reminders,
        "notice_versions": [
            {"version": NOTICE_VERSION, "notice_text": DEMO_NOTICE_TEXT, "effective_from": now}
        ],
    }
    return Dataset(now=now, seed=seed, tables=tables, demo_codes=demo_codes)


def _pick_patient(
    rng: random.Random,
    patient_ids: Sequence[str],
    busy_starts: dict[str, set[datetime]],
    start: datetime,
) -> str:
    """Pick a patient with no other appointment at the same start time."""
    for _ in range(40):
        candidate = rng.choice(patient_ids)
        if start not in busy_starts[candidate]:
            busy_starts[candidate].add(start)
            return candidate
    for candidate in patient_ids:  # fallback keeps generation deterministic
        if start not in busy_starts[candidate]:
            busy_starts[candidate].add(start)
            return candidate
    raise RuntimeError("No free synthetic patient for this time")  # pragma: no cover


def _pick_status(
    rng: random.Random,
    is_past: bool,
    future_index: Optional[int],
    busy_days: int,
    start: datetime,
    now: datetime,
) -> tuple[str, int, bool]:
    """Return (status, delay_minutes, late_cancellation) for a new appointment."""
    roll = rng.random()
    if is_past:
        if roll < 0.82:
            delay = 0 if rng.random() < 0.6 else rng.randint(5, 35)
            return "Completed", delay, False
        if roll < 0.90:
            return "No-show", 0, False
        return "Cancelled", 0, rng.random() < 0.3
    in_busy_window = future_index is not None and future_index < busy_days
    if roll < (0.55 if in_busy_window else 0.50):
        return "Scheduled", 0, False
    if roll < (0.96 if in_busy_window else 0.88):
        return "Confirmed", 0, False
    if in_busy_window or roll < 0.93:
        return "Rescheduled", 0, False  # finalized in _assign_reschedules
    return "Cancelled", 0, is_late_cancellation(start, now)


def _assign_reschedules(
    rng: random.Random,
    now: datetime,
    appointments: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    slot_by_id: dict[str, dict[str, Any]],
    slot_future_index: dict[str, Optional[int]],
    slot_busy_days: dict[str, int],
) -> list[dict[str, Any]]:
    """Give each Rescheduled appointment a released old slot and a history row.

    If no suitable old slot exists the appointment is reset to Scheduled so the
    data stays consistent.
    """
    history: list[dict[str, Any]] = []
    used: set[str] = set()
    for appt in appointments:
        if appt["status"] != "Rescheduled":
            continue
        candidates = sorted(
            s["slot_id"]
            for s in slots
            if s["status"] == "Available"
            and s["start_time"] > now
            and s["type_id"] == appt["type_id"]
            and s["slot_id"] not in used
            and (slot_future_index[s["slot_id"]] or 0) >= slot_busy_days[s["slot_id"]]
        )
        if not candidates:
            appt["status"] = "Scheduled"
            continue
        old_slot_id = rng.choice(candidates)
        used.add(old_slot_id)
        new_slot = slot_by_id[appt["slot_id"]]
        appt["reschedule_count"] = 1
        history.append(
            {
                "appt_id": appt["appt_id"],
                "old_slot_id": old_slot_id,
                "new_slot_id": appt["slot_id"],
                "old_start": slot_by_id[old_slot_id]["start_time"],
                "new_start": new_slot["start_time"],
                "changed_at": now - timedelta(days=rng.randint(1, 3)),
            }
        )
    return history


def summarize(dataset: Dataset) -> dict[str, Any]:
    """Row counts and appointment status counts, for printing and tests."""
    counts = {name: len(rows) for name, rows in dataset.tables.items()}
    by_status: dict[str, int] = {}
    for appt in dataset.tables["appointments"]:
        by_status[appt["status"]] = by_status.get(appt["status"], 0) + 1
    return {"rows": counts, "appointments_by_status": dict(sorted(by_status.items()))}


# --------------------------------------------------------------------------
# Database loading
# --------------------------------------------------------------------------


def apply_schema(conn: Any) -> None:
    """Create tables and indexes (safe to repeat)."""
    conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


def is_database_empty(conn: Any) -> bool:
    """True when no demo rows exist yet."""
    row = conn.execute("SELECT count(*) AS n FROM locations").fetchone()
    return row["n"] == 0


def reset_tables(conn: Any) -> None:
    """Wipe all demo tables (BR retention rule: reset removes consent and audit too)."""
    conn.execute(f"TRUNCATE TABLE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE")


def _insert_rows(conn: Any, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(f"%({c})s" for c in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def load_dataset(conn: Any, dataset: Dataset) -> None:
    """Insert the dataset. Demo access codes are hashed before storage."""
    from db.hashing import hash_access_code

    for table in TABLE_ORDER:
        rows = dataset.tables[table]
        if table == "patient_access":
            rows = [
                {
                    "appt_id": r["appt_id"],
                    "access_code_hash": hash_access_code(dataset.demo_codes[r["appt_id"]]),
                }
                for r in rows
            ]
        _insert_rows(conn, table, rows)


def _print_summary(dataset: Dataset, credentials: int) -> None:
    summary = summarize(dataset)
    print("Loaded synthetic demo data (all values are made up).")
    for table, count in summary["rows"].items():
        print(f"  {table:<20}{count:>6}")
    print("  appointments by status:", summary["appointments_by_status"])
    if credentials > 0:
        print(f"\nDemo sign-in examples (appointment ID / access code), first {credentials}:")
        starts = {s["slot_id"]: s["start_time"] for s in dataset.tables["slots"]}
        appts = {a["appt_id"]: a for a in dataset.tables["appointments"]}
        ordered = sorted(dataset.demo_codes, key=lambda i: (starts[appts[i]["slot_id"]], i))
        for appt_id in ordered[:credentials]:
            when = starts[appts[appt_id]["slot_id"]].strftime("%a %Y-%m-%d %H:%M")
            status = appts[appt_id]["status"]
            print(f"  {appt_id}   {dataset.demo_codes[appt_id]}   ({status}, {when})")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Load synthetic demo data into the database.")
    parser.add_argument(
        "--reset", action="store_true", help="wipe ALL tables in the target database before loading"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO date-time to treat as 'now' (default: today 08:00 office time)",
    )
    parser.add_argument(
        "--credentials", type=int, default=10, help="how many demo sign-in examples to print"
    )
    args = parser.parse_args(argv)

    from config.settings import ConfigError
    from db.connection import DatabaseUnavailableError, close_pool, get_conn

    now = None
    if args.now:
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(OFFICE_TZ))

    dataset = generate_dataset(now=now, seed=args.seed)
    try:
        with get_conn() as conn:
            apply_schema(conn)
            if not is_database_empty(conn):
                if not args.reset:
                    print(
                        "The database already contains data. Re-run with --reset to wipe "
                        "and reload it (demo or test databases only)."
                    )
                    return 2
                reset_tables(conn)
            load_dataset(conn, dataset)
    except (DatabaseUnavailableError, ConfigError) as exc:
        print(f"Could not load data: {exc}")
        return 1
    finally:
        close_pool()

    _print_summary(dataset, args.credentials)
    return 0


if __name__ == "__main__":
    sys.exit(main())
