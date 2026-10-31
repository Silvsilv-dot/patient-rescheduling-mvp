"""Guard test: every business rule (BR-01 to BR-25) must have a named owner.

This fails if a rule is added without an owner, if a listed function no longer
exists, or if a file marked as already built (batch 1) is missing. It stops rules
from being silently dropped between batches.
"""

from __future__ import annotations

from pathlib import Path

from rules import business_rules as br

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_IDS = {f"BR-{n:02d}" for n in range(1, 26)}
BATCH_ONE_FILES = {"rules/business_rules.py", "db/schema.sql", "config/settings.py"}


def test_all_25_rules_are_mapped():
    assert set(br.BR_ENFORCEMENT) == EXPECTED_IDS


def test_every_rule_has_summary_owner_and_valid_batch():
    for rule_id, entry in br.BR_ENFORCEMENT.items():
        assert entry.summary, rule_id
        assert entry.owners, rule_id
        assert 1 <= entry.batch <= 5, rule_id


def test_every_named_symbol_exists_and_is_documented():
    for rule_id, entry in br.BR_ENFORCEMENT.items():
        for symbol in entry.symbols:
            assert hasattr(br, symbol), f"{rule_id}: {symbol} missing"
            target = getattr(br, symbol)
            if callable(target):
                assert target.__doc__, f"{rule_id}: {symbol} has no docstring"


def test_rules_that_only_the_rules_module_owns_have_symbols():
    for rule_id, entry in br.BR_ENFORCEMENT.items():
        if "rules/business_rules.py" in entry.owners:
            assert entry.symbols, rule_id


def test_batch_one_files_named_in_the_map_exist():
    for entry in br.BR_ENFORCEMENT.values():
        for owner in entry.owners:
            if owner in BATCH_ONE_FILES:
                assert (ROOT / owner).is_file(), owner


def test_unique_index_for_br10_is_really_in_the_schema():
    sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    assert "uq_active_appointment_per_slot" in sql
    assert "idempotency_keys" in sql and "audit_log" in sql  # BR-25 and BR-16 tables


def test_rules_owned_by_later_batches_are_listed_for_the_reader():
    later = {r: e.batch for r, e in br.BR_ENFORCEMENT.items() if e.batch > 1}
    assert later  # documents what is still to be built
    assert "BR-09" in later and "BR-25" in later and "BR-16" in later
