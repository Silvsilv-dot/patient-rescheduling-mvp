"""Privacy scan (US-01 criterion 3, test T01-4).

Checks that the schema and the generated data contain no contact details,
record numbers, clinical or billing fields, and only obviously made-up names.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from rules.business_rules import PROHIBITED_FIELD_PARTS as PROHIBITED_NAME_PARTS

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
LONG_NUMBER = re.compile(r"\d{9,}")


def prohibited_schema_names(sql: str) -> set[str]:
    """Identifier parts in the SQL that match the prohibited list (comments ignored)."""
    code = re.sub(r"--[^\n]*", "", sql)
    code = re.sub(r"'[^']*'", "", code)  # string literals
    parts = set()
    for word in re.findall(r"[a-z_][a-z0-9_]*", code.lower()):
        parts.update(word.split("_"))
    return parts & PROHIBITED_NAME_PARTS


def find_pii_patterns(text: str) -> list[str]:
    """Names of PII-like patterns found in a piece of text."""
    hits = []
    for name, pattern in (
        ("email", EMAIL),
        ("phone", PHONE),
        ("ssn", SSN),
        ("long_number", LONG_NUMBER),
    ):
        if pattern.search(text):
            hits.append(name)
    return hits


def test_schema_has_no_prohibited_columns():
    assert prohibited_schema_names(SCHEMA.read_text(encoding="utf-8")) == set()


def test_scanner_detects_bad_schema_and_data():
    assert prohibited_schema_names("CREATE TABLE t (patient_phone TEXT);") == {"phone"}
    assert prohibited_schema_names("CREATE TABLE t (insurance_id TEXT);") == {"insurance"}
    assert prohibited_schema_names("-- phone is only mentioned in a comment\nSELECT 1;") == set()
    assert find_pii_patterns("call 555-123-4567") == ["phone"]
    assert find_pii_patterns("a.b@example.com") == ["email"]
    assert find_pii_patterns("123-45-6789") == ["ssn"]
    assert find_pii_patterns("1234567890") == ["long_number"]
    assert find_pii_patterns("APT-20260001 Demo Patient 01") == []


def test_generated_text_has_no_pii_patterns(dataset):
    for table, rows in dataset.tables.items():
        for row in rows:
            for column, value in row.items():
                if isinstance(value, str):
                    assert find_pii_patterns(value) == [], f"{table}.{column}: {value}"


def test_generated_columns_are_not_prohibited(dataset):
    for table, rows in dataset.tables.items():
        for column in rows[0]:
            assert not (set(column.split("_")) & PROHIBITED_NAME_PARTS), f"{table}.{column}"


def test_names_and_labels_are_obviously_synthetic(dataset):
    t = dataset.tables
    assert all(p["display_name"].startswith("Demo Patient ") for p in t["patients"])
    assert all(p["display_name"].startswith("Dr. Demo ") for p in t["providers"])
    assert all(x["name"].startswith("Demo Clinic ") for x in t["locations"])
    generic = {"Routine Visit", "Follow-up Visit", "Consultation Visit", "Wellness Check"}
    assert {x["label"] for x in t["appointment_types"]} == generic


def test_all_dates_are_relative_to_now(dataset):
    low, high = dataset.now.year - 1, dataset.now.year + 1
    for rows in dataset.tables.values():
        for row in rows:
            for value in row.values():
                if isinstance(value, datetime):
                    assert low <= value.year <= high
