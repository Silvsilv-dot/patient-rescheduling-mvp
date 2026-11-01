"""Repository safety checks for an upload-based workflow.

Fails if a file is missing after an upload, if a real-looking Neon connection
string was pasted into any file, or if .env.example contains a value.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_FILES = [
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    ".github/workflows/ci.yml",
    ".github/workflows/seed-demo-database.yml",
    "api/__init__.py",
    "api/deps.py",
    "api/errors.py",
    "api/main.py",
    "api/routers/__init__.py",
    "api/routers/auth.py",
    "api/routers/consent.py",
    "api/routers/patient.py",
    "api/schemas.py",
    "api/security.py",
    "config/__init__.py",
    "config/settings.py",
    "db/__init__.py",
    "db/connection.py",
    "db/hashing.py",
    "db/schema.sql",
    "db/seed_synthetic.py",
    "rules/__init__.py",
    "rules/business_rules.py",
    "services/__init__.py",
    "services/appointment_service.py",
    "services/audit_service.py",
    "services/auth_service.py",
    "services/consent_service.py",
    "services/exceptions.py",
    "services/reminder_service.py",
    "services/reschedule_service.py",
    "services/slot_service.py",
    "services/wait_time_service.py",
    "tests/__init__.py",
    "tests/api/__init__.py",
    "tests/api/conftest.py",
    "tests/api/helpers.py",
    "tests/api/test_appointments.py",
    "tests/api/test_auth_and_consent.py",
    "tests/api/test_injection_and_limits.py",
    "tests/api/test_reminders.py",
    "tests/api/test_reschedule.py",
    "tests/api/test_security_tokens.py",
    "tests/api/test_slots_and_waits.py",
    "tests/api/test_system_no_db.py",
    "tests/conftest.py",
    "tests/test_config_and_hashing.py",
    "tests/test_db_integration.py",
    "tests/test_enforcement_files_exist.py",
    "tests/test_privacy.py",
    "tests/test_repo_manifest.py",
    "tests/test_rule_enforcement_map.py",
    "tests/test_rules.py",
    "tests/test_rules_plans_and_policies.py",
    "tests/test_seed.py",
]

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv"}
TEXT_SUFFIXES = {".py", ".sql", ".txt", ".toml", ".yml", ".yaml", ".md", ".example", ".cfg"}

# A real Neon host looks like ep-cool-darkness-123456.us-east-2.aws.neon.tech
REAL_NEON_HOST = re.compile(
    r"ep-[a-z0-9]+(-[a-z0-9]+)+(-pooler)?\.[a-z0-9-]+\.(aws|azure)\.neon\.tech"
)


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".env.example"}:
            yield path


def test_all_expected_files_are_present():
    missing = [name for name in EXPECTED_FILES if not (ROOT / name).is_file()]
    assert not missing, (
        "These files are missing from the repository: "
        + ", ".join(missing)
        + ". Upload them to the same branch (same folder and name), then re-run the checks."
    )


def test_no_real_neon_connection_string_in_any_file():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _text_files()
        if path.name != "test_repo_manifest.py"
        and REAL_NEON_HOST.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not offenders, (
        "A real Neon connection string appears in: "
        + ", ".join(offenders)
        + ". Remove it, then reset the database password in Neon, because it is now exposed."
    )


def test_env_example_has_no_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        assert value.strip() == "", f".env.example must not contain a value for {key}"


def test_gitignore_protects_secret_files():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", ".streamlit/secrets.toml"):
        assert pattern in text, f".gitignore must list {pattern}"


def test_workflows_use_current_action_versions():
    for name in (".github/workflows/ci.yml", ".github/workflows/seed-demo-database.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "actions/checkout@v6" in text and "actions/setup-python@v6" in text, name
