"""Guard: every file the rule map says is built by batch 2 really exists.

Pairs with test_rule_enforcement_map.py (which checks batch 1). Later batches
extend the same idea, so a rule cannot point at a file that was never uploaded.
"""

from __future__ import annotations

from pathlib import Path

from rules import business_rules as br

ROOT = Path(__file__).resolve().parent.parent
BUILT_SO_FAR = 2


def test_files_for_rules_completed_by_now_exist():
    missing = []
    for rule_id, entry in br.BR_ENFORCEMENT.items():
        if entry.batch <= BUILT_SO_FAR:
            missing += [
                f"{rule_id}: {owner}" for owner in entry.owners if not (ROOT / owner).is_file()
            ]
    assert not missing, "Rule owner files are missing: " + "; ".join(missing)


def test_the_remaining_rules_are_the_ones_expected():
    later = {rule for rule, entry in br.BR_ENFORCEMENT.items() if entry.batch > BUILT_SO_FAR}
    assert later == {"BR-05", "BR-13", "BR-16", "BR-17", "BR-23", "BR-24"}
