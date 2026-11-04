"""Helpers for the browser tests: run the real API server and describe the seeded data.

The browser tests load the real page from a real server (uvicorn) backed by the test
database, so what is checked is exactly what a patient would use.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SIGNING_KEY = "browser-test-key-" + "b" * 40


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def describe(dataset, now: datetime) -> dict[str, list[dict]]:
    """Sign-in details for seeded appointments, grouped by how far ahead they are."""
    starts = {slot["slot_id"]: slot["start_time"] for slot in dataset.tables["slots"]}
    rows = []
    for appt in sorted(dataset.tables["appointments"], key=lambda a: a["appt_id"]):
        code = dataset.demo_codes.get(appt["appt_id"])
        if not code:
            continue
        rows.append(
            {
                "appt_id": appt["appt_id"],
                "code": code,
                "patient_id": appt["patient_id"],
                "type_id": appt["type_id"],
                "status": appt["status"],
                "reschedule_count": appt["reschedule_count"],
                "hours_ahead": (starts[appt["slot_id"]] - now) / timedelta(hours=1),
            }
        )
    return {
        "far": [r for r in rows if r["hours_ahead"] >= 72 and r["reschedule_count"] == 0],
        "soon": [r for r in rows if 1 <= r["hours_ahead"] <= 20],
        "all": rows,
    }


def office_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


@contextmanager
def running_server(database_url: str, refresh_note: str = "") -> Iterator[str]:
    """Start uvicorn on a free port, wait until it answers, and stop it afterwards."""
    port = free_port()
    env = dict(
        os.environ,
        NEON_DATABASE_URL=database_url,
        TOKEN_SIGNING_KEY=SIGNING_KEY,
    )
    log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api/v1/health", timeout=1):
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                if process.poll() is not None:
                    message = "The server stopped while starting. " + Path(log.name).read_text()
                    raise RuntimeError(message) from None
                time.sleep(0.2)
        else:
            raise RuntimeError("The server did not start in time. " + Path(log.name).read_text())
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        os.unlink(log.name)
