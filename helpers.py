"""Small helpers for choosing test data and signing in."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi.testclient import TestClient

from tests.conftest import FIXED_NOW

ACTIVE = ("Scheduled", "Confirmed", "Rescheduled")


def slot_starts(dataset) -> dict[str, datetime]:
    return {s["slot_id"]: s["start_time"] for s in dataset.tables["slots"]}


def find_appointments(
    dataset,
    *,
    statuses: tuple[str, ...] = ACTIVE,
    min_hours: Optional[float] = None,
    max_hours: Optional[float] = None,
    reschedule_count: Optional[int] = None,
    type_id: Optional[str] = None,
) -> list[dict]:
    """Seeded appointments (that have sign-in codes) matching the filters, in ID order."""
    starts = slot_starts(dataset)
    found = []
    for appt in sorted(dataset.tables["appointments"], key=lambda a: a["appt_id"]):
        if appt["appt_id"] not in dataset.demo_codes or appt["status"] not in statuses:
            continue
        hours = (starts[appt["slot_id"]] - FIXED_NOW) / timedelta(hours=1)
        if min_hours is not None and hours < min_hours:
            continue
        if max_hours is not None and hours > max_hours:
            continue
        if reschedule_count is not None and appt["reschedule_count"] != reschedule_count:
            continue
        if type_id is not None and appt["type_id"] != type_id:
            continue
        found.append(appt)
    return found


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sign_in(client: TestClient, dataset, appt_id: str) -> dict[str, str]:
    """Sign in with the seeded demo code and return the Authorization header."""
    response = client.post(
        "/api/v1/auth/patient",
        json={"appt_id": appt_id, "access_code": dataset.demo_codes[appt_id]},
    )
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def accept_notice(client: TestClient, headers: dict[str, str], version: str = "1.0") -> None:
    response = client.post(
        "/api/v1/consents",
        json={"consent_type": "C1", "status": "Granted", "notice_version": version},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def ready(client: TestClient, dataset, appt_id: str) -> dict[str, str]:
    """Sign in and accept the privacy notice (what the patient page does first)."""
    headers = sign_in(client, dataset, appt_id)
    accept_notice(client, headers)
    return headers


def idem_key() -> str:
    return uuid.uuid4().hex


def reschedule(client: TestClient, headers, appt_id: str, slot_id: str, key: Optional[str] = None):
    return client.post(
        f"/api/v1/appointments/{appt_id}/reschedule",
        json={"new_slot_id": slot_id},
        headers={**headers, "Idempotency-Key": key or idem_key()},
    )


def selectable_slots(
    client: TestClient, headers, appt_id: str, *, min_hours: float = 0.0
) -> list[dict]:
    """Slots the API says can be chosen, optionally only those starting well ahead."""
    body = client.get(f"/api/v1/appointments/{appt_id}/slots", headers=headers).json()
    cutoff = FIXED_NOW + timedelta(hours=min_hours)
    return [
        s
        for s in body["slots"]
        if s["selectable"] and datetime.fromisoformat(s["start_time"]) >= cutoff
    ]
