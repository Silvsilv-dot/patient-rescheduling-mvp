"""Runs the browser tests (real headless Chrome) against a real server and database.

CI sets REQUIRE_UI_TESTS=1, so missing tools make the check FAIL instead of quietly skipping.
Locally, without Node or the browser tools, these tests are skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

ROOT = Path(__file__).resolve().parents[2]
BROWSER_TESTS = ["patient_flow.test.mjs", "theme_and_layout.test.mjs"]


def tools_missing() -> str | None:
    if shutil.which("node") is None:
        return "Node is not installed (needed for the browser tests)"
    if not (ROOT / "node_modules" / "puppeteer-core").is_dir():
        return "browser test tools are not installed (run: npm ci)"
    return None


@pytest.mark.parametrize("script", BROWSER_TESTS)
def test_browser_tests(script, ui_stack):
    reason = tools_missing()
    if reason:
        if os.environ.get("REQUIRE_UI_TESTS"):
            pytest.fail(reason)
        pytest.skip(reason)

    env = dict(
        os.environ,
        UI_BASE=ui_stack["base"],
        UI_CREDS=json.dumps(ui_stack["creds"]),
    )
    result = subprocess.run(
        ["node", "--test", "--test-reporter=spec", str(ROOT / "tests" / "ui" / "browser" / script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    report = result.stdout[-7000:] + result.stderr[-2000:]
    assert result.returncode == 0, "Browser tests failed:\n" + report
