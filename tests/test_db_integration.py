"""Database integration tests (US-01, tests T01-1, T01-3 and BR-10 safeguards).

These run only when TEST_DATABASE_URL points at a *test* database, for example
a separate Neon branch. They WIPE every table in that database, so never point
TEST_DATABASE_URL at the demo or any real database.

    TEST_DATABASE_URL="postgresql://...test-branch..." pytest -m db
"""

from __future__ import annotations

import os

import psycopg
import pytest

from config import settings as cfg
from db import connection, hashing
from db import seed_synthetic as seed
from db.connection import get_conn
from tests.conftest import FIXED_NOW
from tests.test_privacy import PROHIBITED_NAME_PARTS

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
    ),
]


@pytest.fixture(scope="module")
def loaded():
    """Point the app at the test database, load fixed data, and clean up."""
    saved = os.environ.get("NEON_DATABASE_URL")
    os.environ["NEON_DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    cfg.reset_settings_cache()
    connection.close_pool()
    original_iterations = hashing.PBKDF2_ITERATIONS
    hashing.PBKDF2_ITERATIONS = 1000  # keep the test fast
    dataset = seed.generate_dataset(now=FIXED_NOW, seed=2026)
    with get_conn() as conn:
        # Rebuild from scratch so the real schema.sql (indexes included) is what gets tested.
        conn.execute(f"DROP TABLE IF EXISTS {', '.join(reversed(seed.ALL_TABLES))} CASCADE")
        seed.apply_schema(conn)
        seed.load_dataset(conn, dataset)
    yield dataset
    hashing.PBKDF2_ITERATIONS = original_iterations
    connection.close_pool()
    if saved is None:
        os.environ.pop("NEON_DATABASE_URL", None)
    else:
        os.environ["NEON_DATABASE_URL"] = saved
    cfg.reset_settings_cache()


def _count(table: str) -> int:
    with get_conn() as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_health_check_ok(loaded):
    assert connection.health_check() == {"status": "ok", "database": "ok"}
    assert connection.warm_up()


def test_schema_can_be_applied_twice(loaded):
    with get_conn() as conn:
        seed.apply_schema(conn)
        seed.apply_schema(conn)


def test_row_counts_match_dataset(loaded):
    for table in seed.TABLE_ORDER:
        assert _count(table) == len(loaded.tables[table]), table


def test_timestamps_round_trip_as_utc_instants(loaded):
    first = loaded.tables["slots"][0]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT start_time FROM slots WHERE slot_id = %s", (first["slot_id"],)
        ).fetchone()
    assert row["start_time"] == first["start_time"]  # same instant, any zone


def test_active_appointment_cannot_share_a_slot(loaded):  # BR-10 safeguard
    active = next(
        a
        for a in loaded.tables["appointments"]
        if a["status"] in ("Scheduled", "Confirmed", "Rescheduled")
    )
    other_patient = next(
        p["patient_id"]
        for p in loaded.tables["patients"]
        if p["patient_id"] != active["patient_id"]
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO appointments (appt_id, patient_id, slot_id, type_id, status) "
                "VALUES ('APT-DUP', %s, %s, %s, 'Scheduled')",
                (other_patient, active["slot_id"], active["type_id"]),
            )
    assert _count("appointments") == len(loaded.tables["appointments"])


def test_cancelled_appointment_does_not_block_the_slot(loaded):
    cancelled = next(a for a in loaded.tables["appointments"] if a["status"] == "Cancelled")
    patient = cancelled["patient_id"]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO appointments (appt_id, patient_id, slot_id, type_id, status) "
            "VALUES ('APT-REUSE', %s, %s, %s, 'Scheduled')",
            (patient, cancelled["slot_id"], cancelled["type_id"]),
        )
        conn.rollback()  # leave the data unchanged


def test_check_constraints_reject_bad_values(loaded):
    with pytest.raises(psycopg.errors.CheckViolation):
        with get_conn() as conn:
            conn.execute(
                "UPDATE appointments SET status = 'Pending' WHERE appt_id = %s",
                (loaded.tables["appointments"][0]["appt_id"],),
            )
    with pytest.raises(psycopg.errors.CheckViolation):
        with get_conn() as conn:
            conn.execute("UPDATE reminders SET status = 'Sent'")


def test_transaction_rolls_back_on_error(loaded):
    before = _count("audit_log")
    with pytest.raises(RuntimeError):
        with get_conn() as conn:
            conn.execute("INSERT INTO audit_log (actor_role, action) VALUES ('Manager', 'test')")
            raise RuntimeError("boom")
    assert _count("audit_log") == before


def test_idempotency_key_is_unique_per_endpoint_and_subject(loaded):
    insert = (
        "INSERT INTO idempotency_keys "
        "(idem_key, endpoint, subject_id, request_hash, status_code, response_body) "
        "VALUES ('key-0123456789abcdef', 'reschedule', 'APT-1', 'h', 200, '{}'::jsonb)"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with get_conn() as conn:
            conn.execute(insert)
            conn.execute(insert)
    assert _count("idempotency_keys") == 0


def test_access_codes_are_stored_hashed_and_verify(loaded):
    appt_id, code = next(iter(loaded.demo_codes.items()))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT access_code_hash FROM patient_access WHERE appt_id = %s", (appt_id,)
        ).fetchone()
    assert code not in row["access_code_hash"]
    assert hashing.verify_access_code(code, row["access_code_hash"])
    assert not hashing.verify_access_code("000000x", row["access_code_hash"])


def test_database_columns_have_no_prohibited_names(loaded):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        ).fetchall()
    assert rows
    for row in rows:
        assert not (set(row["column_name"].split("_")) & PROHIBITED_NAME_PARTS), row


def test_seed_cli_refuses_to_overwrite_without_reset(loaded):
    assert seed.main(["--credentials", "0"]) == 2
    assert _count("appointments") == len(loaded.tables["appointments"])


def test_seed_cli_reset_reproduces_same_state(loaded, capsys):  # T01-3
    def snapshot():
        with get_conn() as conn:
            return [
                r["appt_id"]
                for r in conn.execute(
                    "SELECT appt_id FROM appointments ORDER BY appt_id LIMIT 10"
                ).fetchall()
            ], {t: _count(t) for t in seed.TABLE_ORDER}

    original_iterations = hashing.PBKDF2_ITERATIONS
    hashing.PBKDF2_ITERATIONS = 1000
    first = snapshot()
    assert seed.main(["--reset", "--now", "2026-11-02T08:00:00", "--credentials", "3"]) == 0
    second = snapshot()
    assert seed.main(["--reset", "--now", "2026-11-02T08:00:00", "--credentials", "0"]) == 0
    third = snapshot()
    hashing.PBKDF2_ITERATIONS = original_iterations
    assert first == second == third
    out = capsys.readouterr().out
    assert "Loaded synthetic demo data" in out and "APT-2026" in out
