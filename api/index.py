"""HealthFlow Clinic Showcase API & Static Server (Vercel + Neon Postgres)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db.connection import get_db_cursor
from rules.business_rules import (
    CONSENT_C1_VERSION,
    CONSENT_C3_VERSION,
    MIN_SLOTS_FALLBACK,
    assert_no_prohibited_fields,
    classify_slot_availability,
    classify_utilization,
    evaluate_appointment_actions,
    validate_idempotency_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_PORTAL_DIR = BASE_DIR / "static" / "portal"

app = FastAPI(title="HealthFlow Clinical Operations Showcase", version="2.0.0")


@app.middleware("http")
async def security_and_privacy_headers(request: Request, call_next):
    # Enforce 16KB body size cap (BR-22)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 16384:
        return JSONResponse(
            status_code=413,
            content={"error": {"code": "payload_too_large", "message": "Request exceeds 16KB limit."}},
        )

    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def safe_json(payload: Any) -> JSONResponse:
    assert_no_prohibited_fields(payload)
    return JSONResponse(content=payload)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# --- Request Models ---
class ConsentC1Request(BaseModel):
    patient_id: str
    accept_privacy_terms: bool
    opt_in_reminders: bool = True


class ConsentC3Request(BaseModel):
    role: str
    persona_key: str


class RescheduleRequest(BaseModel):
    new_slot_id: str
    actor_role: str = "Patient"
    actor_alias: str = "Patient Self-Service"


class CancelRequest(BaseModel):
    confirm: bool
    actor_role: str = "Patient"
    actor_alias: str = "Patient Self-Service"


class CheckinRequest(BaseModel):
    room_status: str
    actor_role: str = "FrontDesk"


class DelayRequest(BaseModel):
    provider_id: str
    delay_minutes: int = Field(ge=0, le=180)
    reason_code: str = "CLINIC_FLOW"
    actor_role: str = "Clinician"


# --- Health & Demo Personas ---
@app.get("/api/v1/health")
def health_check():
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM appointments;")
            row = cur.fetchone()
        return safe_json({"status": "ok", "db_connected": True, "appointment_count": row["cnt"]})
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "db_unavailable", "message": "Database connection pending configuration."}},
        )


@app.get("/api/v1/demo/personas")
def list_demo_personas():
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT persona_key, role, display_label, display_label_es,
                   scenario_tag, scenario_tag_es, target_id
            FROM demo_personas
            ORDER BY CASE role
              WHEN 'Patient' THEN 1
              WHEN 'FrontDesk' THEN 2
              WHEN 'Clinician' THEN 3
              WHEN 'Manager' THEN 4
            END, persona_key;
            """
        )
        rows = cur.fetchall()
    return safe_json({"personas": rows, "base_time_zone": "America/New_York"})


# --- Consent Endpoints (BR-18, BR-19, BR-24) ---
@app.post("/api/v1/consent/c1")
def grant_c1_consent(body: ConsentC1Request):
    if not body.accept_privacy_terms:
        raise HTTPException(status_code=400, detail="Privacy & Terms acceptance is required.")
    now = _now()
    with get_db_cursor() as cur:
        cur.execute(
            """
            UPDATE patients
            SET consent_c1_version = %s,
                consent_c1_granted_at = %s,
                consent_c2_reminders = %s,
                consent_c2_updated_at = %s
            WHERE patient_id = %s
            RETURNING patient_id, consent_c1_version, consent_c2_reminders;
            """,
            (CONSENT_C1_VERSION, now, body.opt_in_reminders, now, body.patient_id),
        )
        patient = cur.fetchone()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient record not found.")

        # BR-19: Suppress or enable reminders across all active appointments
        new_rem_status = "Simulated-Pending" if body.opt_in_reminders else "Suppressed"
        cur.execute(
            """
            UPDATE reminders r
            SET status = %s
            FROM appointments a
            WHERE r.appt_id = a.appt_id
              AND a.patient_id = %s
              AND a.status IN ('Scheduled', 'Confirmed', 'Rescheduled');
            """,
            (new_rem_status, body.patient_id),
        )
        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, detail)
            VALUES ('Patient', %s, 'CONSENT_UPDATED', %s);
            """,
            (
                body.patient_id,
                f"C1={CONSENT_C1_VERSION} granted; C2 reminders={'ON' if body.opt_in_reminders else 'OFF'} (BR-18/BR-19)",
            ),
        )
    return safe_json({"ok": True, "patient": patient})


@app.post("/api/v1/consent/c3")
def record_c3_consent(body: ConsentC3Request):
    if body.role not in {"FrontDesk", "Clinician", "Manager"}:
        raise HTTPException(status_code=400, detail="Invalid staff role.")
    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO staff_consents (role, persona_key, c3_version)
            VALUES (%s, %s, %s);
            """,
            (body.role, body.persona_key, CONSENT_C3_VERSION),
        )
        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, detail)
            VALUES (%s, %s, 'C3_CONFIDENTIALITY_ACK', %s);
            """,
            (body.role, body.persona_key, f"Staff confidentiality notice {CONSENT_C3_VERSION} acknowledged (BR-24)"),
        )
    return safe_json({"ok": True, "c3_version": CONSENT_C3_VERSION})


# --- View 1: Patient Portal Endpoints (BR-01..BR-12, BR-18..BR-21) ---
@app.get("/api/v1/patient/{patient_id}/overview")
def get_patient_overview(patient_id: str):
    now = _now()
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT patient_id, monogram, display_alias, consent_c1_version,
                   consent_c1_granted_at, consent_c2_reminders
            FROM patients WHERE patient_id = %s;
            """,
            (patient_id,),
        )
        patient = cur.fetchone()
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found.")

        cur.execute(
            """
            SELECT a.appt_id, a.slot_id, a.type_id, a.status, a.reschedule_count, a.late_cancellation,
                   s.start_time, s.end_time,
                   t.label AS visit_label, t.label_es AS visit_label_es,
                   l.name AS location_name, l.suite AS location_suite, l.time_zone,
                   COALESCE(d.delay_minutes, 0) AS active_delay_minutes
            FROM appointments a
            JOIN slots s ON a.slot_id = s.slot_id
            JOIN appointment_types t ON a.type_id = t.type_id
            JOIN providers p ON s.provider_id = p.provider_id
            JOIN locations l ON p.location_id = l.location_id
            LEFT JOIN LATERAL (
                SELECT delay_minutes FROM provider_delays pd
                WHERE pd.provider_id = p.provider_id AND pd.active = TRUE
                ORDER BY created_at DESC LIMIT 1
            ) d ON TRUE
            WHERE a.patient_id = %s
            ORDER BY s.start_time ASC;
            """,
            (patient_id,),
        )
        rows = cur.fetchall()

    upcoming = []
    past = []
    for r in rows:
        start_dt = r["start_time"]
        elig = evaluate_appointment_actions(
            status=r["status"],
            start_time=start_dt,
            reschedule_count=r["reschedule_count"],
            now=now,
            is_staff_override=False,
        )
        # BR-03 Minimum Necessary: Patient view omits provider name & patient PII
        item = {
            "appt_id": r["appt_id"],
            "slot_id": r["slot_id"],
            "type_id": r["type_id"],
            "status": r["status"],
            "reschedule_count": r["reschedule_count"],
            "max_reschedules": 2,
            "late_cancellation": r["late_cancellation"],
            "start_time": _serialize_dt(start_dt),
            "end_time": _serialize_dt(r["end_time"]),
            "time_zone": r["time_zone"],
            "visit_label": r["visit_label"],
            "visit_label_es": r["visit_label_es"],
            "location_name": r["location_name"],
            "location_suite": r["location_suite"],
            "active_delay_minutes": r["active_delay_minutes"],
            "allowed_actions": {
                "can_confirm": elig.can_confirm,
                "can_reschedule": elig.can_reschedule,
                "can_cancel": elig.can_cancel,
                "lock_reason_code": elig.lock_reason_code,
                "hours_until_start": elig.hours_until_start,
                "is_late_cancel_window": elig.is_late_cancel_window,
            },
        }
        if r["status"] in {"Scheduled", "Confirmed", "Rescheduled"} and start_dt >= now:
            upcoming.append(item)
        else:
            past.append(item)

    past.reverse()
    return safe_json({
        "patient": {
            "patient_id": patient["patient_id"],
            "monogram": patient["monogram"],
            "display_alias": patient["display_alias"],
            "consent_c1_valid": patient["consent_c1_version"] == CONSENT_C1_VERSION,
            "consent_c2_reminders": patient["consent_c2_reminders"],
        },
        "upcoming_appointments": upcoming,
        "past_appointments": past[:10],
    })


@app.get("/api/v1/appointments/{appt_id}/slots")
def list_reschedule_slots(appt_id: str, staff_override: bool = Query(default=False)):
    now = _now()
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT a.appt_id, a.patient_id, a.slot_id, a.type_id, a.status, a.reschedule_count,
                   s.start_time
            FROM appointments a
            JOIN slots s ON a.slot_id = s.slot_id
            WHERE a.appt_id = %s;
            """,
            (appt_id,),
        )
        appt = cur.fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found.")

        # Fetch other active appointments for conflict check (BR-06)
        cur.execute(
            """
            SELECT s.start_time, s.end_time
            FROM appointments a
            JOIN slots s ON a.slot_id = s.slot_id
            WHERE a.patient_id = %s
              AND a.appt_id <> %s
              AND a.status IN ('Scheduled', 'Confirmed', 'Rescheduled');
            """,
            (appt["patient_id"], appt_id),
        )
        other_intervals = [(row["start_time"], row["end_time"]) for row in cur.fetchall()]

        # Fetch all same-type slots in 14-day window (BR-04, BR-05)
        cur.execute(
            """
            SELECT s.slot_id, s.provider_id, s.type_id, s.start_time, s.end_time, s.status,
                   p.display_name AS provider_name,
                   l.name AS location_name, l.suite AS location_suite
            FROM slots s
            JOIN providers p ON s.provider_id = p.provider_id
            JOIN locations l ON p.location_id = l.location_id
            WHERE s.type_id = %s
              AND s.start_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'
              AND s.start_time <= NOW() + INTERVAL '14 days'
            ORDER BY s.start_time ASC, l.name ASC;
            """,
            (appt["type_id"],),
        )
        raw_slots = cur.fetchall()

    formatted_slots = []
    available_count = 0
    wait_by_option: dict[str, dict[str, Any]] = {}

    for idx, s in enumerate(raw_slots):
        is_avail, reason = classify_slot_availability(
            slot_status=s["status"],
            slot_start=s["start_time"],
            slot_end=s["end_time"],
            slot_type_id=s["type_id"],
            required_type_id=appt["type_id"],
            current_slot_id=appt["slot_id"],
            slot_id=s["slot_id"],
            other_active_intervals=other_intervals,
            now=now,
        )
        if is_avail:
            available_count += 1
            opt_key = s["location_name"] if not staff_override else f"{s['provider_name']} ({s['location_name']})"
            if opt_key not in wait_by_option:
                days_wait = max(0, round((s["start_time"] - now).total_seconds() / 86400.0, 1))
                wait_by_option[opt_key] = {
                    "option_label": opt_key,
                    "earliest_start": _serialize_dt(s["start_time"]),
                    "wait_days": days_wait,
                }

        formatted_slots.append({
            "slot_id": s["slot_id"],
            "start_time": _serialize_dt(s["start_time"]),
            "end_time": _serialize_dt(s["end_time"]),
            "location_name": s["location_name"],
            "location_suite": s["location_suite"],
            # BR-03: Mask clinician name on patient self-service view; show on staff view
            "provider_label": s["provider_name"] if staff_override else f"Care Team · {s['location_name']}",
            "available": is_avail,
            "unavailable_reason": reason,
        })

    return safe_json({
        "appt_id": appt_id,
        "available_count": available_count,
        "low_availability_fallback": available_count < MIN_SLOTS_FALLBACK,
        "wait_time_comparison": list(wait_by_option.values()),
        "slots": formatted_slots,
    })


@app.post("/api/v1/appointments/{appt_id}/confirm")
def confirm_appointment(appt_id: str, idempotency_key: str | None = Header(default=None)):
    key = validate_idempotency_key(idempotency_key)
    now = _now()
    with get_db_cursor() as cur:
        cur.execute("SELECT response_payload FROM idempotency_keys WHERE idempotency_key = %s;", (key,))
        cached = cur.fetchone()
        if cached:
            return safe_json(cached["response_payload"])

        cur.execute(
            """
            SELECT a.appt_id, a.status, a.reschedule_count, s.start_time
            FROM appointments a JOIN slots s ON a.slot_id = s.slot_id
            WHERE a.appt_id = %s FOR UPDATE;
            """,
            (appt_id,),
        )
        appt = cur.fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found.")

        elig = evaluate_appointment_actions(appt["status"], appt["start_time"], appt["reschedule_count"], now)
        if not elig.can_confirm:
            raise HTTPException(status_code=409, detail="Appointment cannot be confirmed in its current state.")

        cur.execute(
            "UPDATE appointments SET status = 'Confirmed', updated_at = %s WHERE appt_id = %s;",
            (now, appt_id),
        )
        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, appt_id, detail, idempotency_prefix)
            VALUES ('Patient', 'Patient Self-Service', 'CONFIRMED', %s, 'Visit confirmed by patient (BR-01)', %s);
            """,
            (appt_id, key[:10]),
        )
        resp = {"ok": True, "appt_id": appt_id, "status": "Confirmed"}
        cur.execute(
            """
            INSERT INTO idempotency_keys (idempotency_key, request_fingerprint, response_payload)
            VALUES (%s, %s, %s::jsonb);
            """,
            (key, hashlib.sha256(f"confirm:{appt_id}".encode()).hexdigest(), json.dumps(resp)),
        )
    return safe_json(resp)


@app.post("/api/v1/appointments/{appt_id}/reschedule")
def reschedule_appointment(
    appt_id: str,
    body: RescheduleRequest,
    idempotency_key: str | None = Header(default=None),
):
    key = validate_idempotency_key(idempotency_key)
    fingerprint = hashlib.sha256(f"reschedule:{appt_id}:{body.new_slot_id}".encode()).hexdigest()
    now = _now()
    is_staff = body.actor_role in {"FrontDesk", "Clinician", "Manager"}

    with get_db_cursor() as cur:
        cur.execute(
            "SELECT request_fingerprint, response_payload FROM idempotency_keys WHERE idempotency_key = %s;",
            (key,),
        )
        cached = cur.fetchone()
        if cached:
            if cached["request_fingerprint"] != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency key reused with different payload (BR-25).")
            return safe_json(cached["response_payload"])

        # Lock appointment & target slot in deterministic order (BR-09, BR-10)
        cur.execute(
            """
            SELECT a.appt_id, a.patient_id, a.slot_id AS old_slot_id, a.type_id, a.status, a.reschedule_count,
                   s.start_time AS old_start_time
            FROM appointments a
            JOIN slots s ON a.slot_id = s.slot_id
            WHERE a.appt_id = %s
            FOR UPDATE OF a;
            """,
            (appt_id,),
        )
        appt = cur.fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found.")

        elig = evaluate_appointment_actions(
            appt["status"], appt["old_start_time"], appt["reschedule_count"], now, is_staff_override=is_staff
        )
        if not elig.can_reschedule:
            raise HTTPException(status_code=409, detail=f"Reschedule blocked ({elig.lock_reason_code}).")

        cur.execute(
            """
            SELECT slot_id, type_id, start_time, end_time, status
            FROM slots WHERE slot_id = %s FOR UPDATE;
            """,
            (body.new_slot_id,),
        )
        target = cur.fetchone()
        if not target or target["status"] != "Available" or target["type_id"] != appt["type_id"] or target["start_time"] <= now:
            raise HTTPException(status_code=409, detail="Selected slot is no longer available (BR-06/BR-10).")

        # Claim new slot, release old slot, update appointment atomically
        cur.execute("UPDATE slots SET status = 'Booked' WHERE slot_id = %s AND status = 'Available';", (body.new_slot_id,))
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="Concurrent booking conflict prevented (BR-10).")

        cur.execute("UPDATE slots SET status = 'Available' WHERE slot_id = %s;", (appt["old_slot_id"],))

        new_count = appt["reschedule_count"] if is_staff else appt["reschedule_count"] + 1
        cur.execute(
            """
            UPDATE appointments
            SET slot_id = %s, status = 'Rescheduled', reschedule_count = %s, updated_at = %s
            WHERE appt_id = %s;
            """,
            (body.new_slot_id, new_count, now, appt_id),
        )

        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, appt_id, old_slot_id, new_slot_id, detail, idempotency_prefix)
            VALUES (%s, %s, 'RESCHEDULED', %s, %s, %s, %s, %s);
            """,
            (
                body.actor_role if is_staff else "Patient",
                body.actor_alias,
                appt_id,
                appt["old_slot_id"],
                body.new_slot_id,
                f"Atomic reschedule completed ({new_count}/2 used) (BR-09/BR-10)",
                key[:10],
            ),
        )

        resp = {
            "ok": True,
            "appt_id": appt_id,
            "status": "Rescheduled",
            "old_slot_id": appt["old_slot_id"],
            "new_slot_id": body.new_slot_id,
            "reschedule_count": new_count,
        }
        cur.execute(
            """
            INSERT INTO idempotency_keys (idempotency_key, request_fingerprint, response_payload)
            VALUES (%s, %s, %s::jsonb);
            """,
            (key, fingerprint, json.dumps(resp)),
        )
    return safe_json(resp)


@app.post("/api/v1/appointments/{appt_id}/cancel")
def cancel_appointment(
    appt_id: str,
    body: CancelRequest,
    idempotency_key: str | None = Header(default=None),
):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required to cancel (BR-11).")
    key = validate_idempotency_key(idempotency_key)
    now = _now()
    is_staff = body.actor_role in {"FrontDesk", "Clinician", "Manager"}

    with get_db_cursor() as cur:
        cur.execute("SELECT response_payload FROM idempotency_keys WHERE idempotency_key = %s;", (key,))
        cached = cur.fetchone()
        if cached:
            return safe_json(cached["response_payload"])

        cur.execute(
            """
            SELECT a.appt_id, a.slot_id, a.status, a.reschedule_count, s.start_time
            FROM appointments a JOIN slots s ON a.slot_id = s.slot_id
            WHERE a.appt_id = %s FOR UPDATE OF a;
            """,
            (appt_id,),
        )
        appt = cur.fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found.")

        elig = evaluate_appointment_actions(appt["status"], appt["start_time"], appt["reschedule_count"], now)
        if not elig.can_cancel:
            raise HTTPException(status_code=409, detail="Appointment cannot be cancelled.")

        cur.execute(
            """
            UPDATE appointments
            SET status = 'Cancelled', late_cancellation = %s, updated_at = %s
            WHERE appt_id = %s;
            """,
            (elig.is_late_cancel_window, now, appt_id),
        )
        cur.execute("UPDATE slots SET status = 'Available' WHERE slot_id = %s;", (appt["slot_id"],))
        cur.execute("DELETE FROM reminders WHERE appt_id = %s;", (appt_id,))
        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, appt_id, old_slot_id, detail, idempotency_prefix)
            VALUES (%s, %s, 'CANCELLED', %s, %s, %s, %s);
            """,
            (
                body.actor_role if is_staff else "Patient",
                body.actor_alias,
                appt_id,
                appt["slot_id"],
                f"Cancelled ({'Late <24h' if elig.is_late_cancel_window else 'Standard'}); slot released (BR-11)",
                key[:10],
            ),
        )
        resp = {
            "ok": True,
            "appt_id": appt_id,
            "status": "Cancelled",
            "late_cancellation": elig.is_late_cancel_window,
        }
        cur.execute(
            """
            INSERT INTO idempotency_keys (idempotency_key, request_fingerprint, response_payload)
            VALUES (%s, %s, %s::jsonb);
            """,
            (key, hashlib.sha256(f"cancel:{appt_id}".encode()).hexdigest(), json.dumps(resp)),
        )
    return safe_json(resp)


# --- View 2: Front-Desk Staff Triage & Coordination Endpoints ---
@app.get("/api/v1/staff/triage")
def get_staff_triage(location_id: str = Query(default="ALL")):
    with get_db_cursor() as cur:
        loc_filter = ""
        params: list[Any] = []
        if location_id and location_id != "ALL":
            loc_filter = "AND l.location_id = %s"
            params.append(location_id)

        cur.execute(
            f"""
            SELECT a.appt_id, a.status, a.reschedule_count, a.late_cancellation,
                   pt.monogram, pt.display_alias,
                   s.slot_id, s.start_time, s.end_time,
                   t.label AS specialty_label, t.label_es AS specialty_label_es,
                   p.provider_id, p.display_name AS provider_name,
                   l.location_id, l.name AS location_name, l.suite AS location_suite,
                   COALESCE(vc.room_status, 'Not Arrived') AS room_status,
                   COALESCE(d.delay_minutes, 0) AS delay_minutes
            FROM appointments a
            JOIN patients pt ON a.patient_id = pt.patient_id
            JOIN slots s ON a.slot_id = s.slot_id
            JOIN appointment_types t ON a.type_id = t.type_id
            JOIN providers p ON s.provider_id = p.provider_id
            JOIN locations l ON p.location_id = l.location_id
            LEFT JOIN visit_checkins vc ON a.appt_id = vc.appt_id
            LEFT JOIN LATERAL (
                SELECT delay_minutes FROM provider_delays pd
                WHERE pd.provider_id = p.provider_id AND pd.active = TRUE
                ORDER BY created_at DESC LIMIT 1
            ) d ON TRUE
            WHERE s.start_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'
              AND s.start_time <= NOW() + INTERVAL '7 days'
              AND a.status IN ('Scheduled', 'Confirmed', 'Rescheduled', 'Completed')
              {loc_filter}
            ORDER BY s.start_time ASC, a.appt_id ASC
            LIMIT 60;
            """,
            params,
        )
        roster = cur.fetchall()

        # Quick drop targets (next open slots for drag-and-drop reassignment)
        cur.execute(
            f"""
            SELECT s.slot_id, s.start_time, p.provider_id, p.display_name AS provider_name,
                   l.name AS location_name, t.label AS specialty_label
            FROM slots s
            JOIN providers p ON s.provider_id = p.provider_id
            JOIN locations l ON p.location_id = l.location_id
            JOIN appointment_types t ON s.type_id = t.type_id
            WHERE s.status = 'Available'
              AND s.start_time > NOW()
              AND s.start_time <= NOW() + INTERVAL '5 days'
              {loc_filter}
            ORDER BY s.start_time ASC
            LIMIT 12;
            """,
            params,
        )
        open_slots = cur.fetchall()

    total_bookings = len(roster)
    confirmed_visits = sum(1 for r in roster if r["status"] in {"Confirmed", "Completed"})
    pending_action = sum(1 for r in roster if r["status"] in {"Scheduled", "Rescheduled"})
    clinician_load = min(98, round((total_bookings / max(1, total_bookings + len(open_slots))) * 100))

    serialized_roster = []
    for r in roster:
        item = dict(r)
        item["start_time"] = _serialize_dt(r["start_time"])
        item["end_time"] = _serialize_dt(r["end_time"])
        serialized_roster.append(item)

    serialized_open = []
    for o in open_slots:
        item = dict(o)
        item["start_time"] = _serialize_dt(o["start_time"])
        serialized_open.append(item)

    return safe_json({
        "kpis": {
            "total_bookings": total_bookings,
            "confirmed_visits": confirmed_visits,
            "pending_action": pending_action,
            "clinician_load_pct": clinician_load,
        },
        "roster": serialized_roster,
        "quick_open_slots": serialized_open,
    })


@app.post("/api/v1/staff/appointments/{appt_id}/checkin")
def update_visit_checkin(appt_id: str, body: CheckinRequest):
    valid_states = {"Not Arrived", "Checked In", "Roomed", "In Visit", "Completed", "No-Show"}
    if body.room_status not in valid_states:
        raise HTTPException(status_code=400, detail="Invalid room status.")
    now = _now()
    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO visit_checkins (appt_id, room_status, updated_by_role, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (appt_id) DO UPDATE
            SET room_status = EXCLUDED.room_status,
                updated_by_role = EXCLUDED.updated_by_role,
                updated_at = EXCLUDED.updated_at;
            """,
            (appt_id, body.room_status, body.actor_role, now),
        )
        if body.room_status == "Completed":
            cur.execute("UPDATE appointments SET status = 'Completed', updated_at = %s WHERE appt_id = %s;", (now, appt_id))
        elif body.room_status == "No-Show":
            cur.execute("UPDATE appointments SET status = 'No-show', updated_at = %s WHERE appt_id = %s;", (now, appt_id))

        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, appt_id, detail)
            VALUES (%s, %s, 'STATUS_UPDATED', %s, %s);
            """,
            (body.actor_role, f"{body.actor_role} Console", appt_id, f"Visit status updated to {body.room_status}"),
        )
    return safe_json({"ok": True, "appt_id": appt_id, "room_status": body.room_status})


# --- View 3: Clinician Workspace Endpoints ---
@app.get("/api/v1/clinician/{provider_id}/schedule")
def get_clinician_schedule(provider_id: str):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT p.provider_id, p.display_name, p.specialty, p.specialty_es,
                   l.name AS location_name, l.suite AS location_suite
            FROM providers p
            JOIN locations l ON p.location_id = l.location_id
            WHERE p.provider_id = %s;
            """,
            (provider_id,),
        )
        provider = cur.fetchone()
        if not provider:
            raise HTTPException(status_code=404, detail="Clinician not found.")

        cur.execute(
            """
            SELECT delay_minutes, reason_code, created_at
            FROM provider_delays
            WHERE provider_id = %s AND active = TRUE
            ORDER BY created_at DESC LIMIT 1;
            """,
            (provider_id,),
        )
        delay_row = cur.fetchone()

        cur.execute(
            """
            SELECT s.slot_id, s.start_time, s.end_time, s.status AS slot_status,
                   a.appt_id, a.status AS appt_status, a.reschedule_count,
                   pt.monogram, pt.display_alias,
                   t.label AS visit_label, t.label_es AS visit_label_es,
                   COALESCE(vc.room_status, 'Not Arrived') AS room_status
            FROM slots s
            LEFT JOIN appointments a ON a.slot_id = s.slot_id AND a.status <> 'Cancelled'
            LEFT JOIN patients pt ON a.patient_id = pt.patient_id
            LEFT JOIN appointment_types t ON s.type_id = t.type_id
            LEFT JOIN visit_checkins vc ON a.appt_id = vc.appt_id
            WHERE s.provider_id = %s
              AND s.start_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'
              AND s.start_time <= NOW() + INTERVAL '3 days'
            ORDER BY s.start_time ASC
            LIMIT 36;
            """,
            (provider_id,),
        )
        timeline = cur.fetchall()

    items = []
    for row in timeline:
        d = dict(row)
        d["start_time"] = _serialize_dt(row["start_time"])
        d["end_time"] = _serialize_dt(row["end_time"])
        items.append(d)

    return safe_json({
        "clinician": provider,
        "active_delay_minutes": delay_row["delay_minutes"] if delay_row else 0,
        "timeline": items,
    })


@app.post("/api/v1/clinician/delays")
def log_provider_delay(body: DelayRequest):
    with get_db_cursor() as cur:
        cur.execute("UPDATE provider_delays SET active = FALSE WHERE provider_id = %s;", (body.provider_id,))
        if body.delay_minutes > 0:
            cur.execute(
                """
                INSERT INTO provider_delays (provider_id, delay_minutes, reason_code, active, logged_by_role)
                VALUES (%s, %s, %s, TRUE, %s);
                """,
                (body.provider_id, body.delay_minutes, body.reason_code, body.actor_role),
            )
        cur.execute(
            """
            INSERT INTO audit_logs (actor_role, actor_alias, action, detail)
            VALUES (%s, %s, 'DELAY_BROADCAST', %s);
            """,
            (
                body.actor_role,
                body.provider_id,
                f"Provider {body.provider_id} delay set to +{body.delay_minutes} min (BR-13)",
            ),
        )
    return safe_json({"ok": True, "provider_id": body.provider_id, "delay_minutes": body.delay_minutes})


# --- View 4: Clinic Manager 6-Month Analytics & Immutable Audit Trail (BR-13, BR-16) ---
@app.get("/api/v1/manager/analytics")
def get_manager_analytics(window: str = Query(default="180d")):
    interval_map = {"7d": "7 days", "30d": "30 days", "180d": "180 days", "future14d": "14 days"}
    if window not in interval_map:
        window = "180d"

    time_clause = (
        "s.start_time >= NOW() AND s.start_time <= NOW() + INTERVAL '14 days'"
        if window == "future14d"
        else f"s.start_time >= NOW() - INTERVAL '{interval_map[window]}' AND s.start_time <= NOW() + INTERVAL '14 days'"
    )

    with get_db_cursor() as cur:
        cur.execute(
            f"""
            SELECT
              COUNT(*) AS total_slots,
              COUNT(*) FILTER (WHERE s.status = 'Booked') AS booked_slots,
              COUNT(*) FILTER (WHERE a.status = 'Completed') AS completed_count,
              COUNT(*) FILTER (WHERE a.status = 'Rescheduled' OR a.reschedule_count > 0) AS rescheduled_count,
              COUNT(*) FILTER (WHERE a.status = 'Cancelled') AS cancelled_count,
              COUNT(*) FILTER (WHERE a.status = 'Cancelled' AND a.late_cancellation = TRUE) AS late_cancel_count,
              COUNT(*) FILTER (WHERE a.status = 'No-show') AS noshow_count
            FROM slots s
            LEFT JOIN appointments a ON a.slot_id = s.slot_id
            WHERE {time_clause};
            """
        )
        agg = cur.fetchone()

        # Provider utilization breakdown (BR-13)
        cur.execute(
            f"""
            SELECT p.provider_id, p.display_name, p.specialty, l.name AS location_name,
                   COUNT(s.slot_id) AS total_slots,
                   COUNT(s.slot_id) FILTER (WHERE s.status = 'Booked') AS booked_slots,
                   COALESCE(d.delay_minutes, 0) AS active_delay_minutes
            FROM providers p
            JOIN locations l ON p.location_id = l.location_id
            LEFT JOIN slots s ON s.provider_id = p.provider_id AND {time_clause}
            LEFT JOIN LATERAL (
                SELECT delay_minutes FROM provider_delays pd
                WHERE pd.provider_id = p.provider_id AND pd.active = TRUE
                ORDER BY created_at DESC LIMIT 1
            ) d ON TRUE
            GROUP BY p.provider_id, p.display_name, p.specialty, l.name, d.delay_minutes
            ORDER BY p.provider_id;
            """
        )
        providers = cur.fetchall()

        # Monthly 6-month trend bars
        cur.execute(
            """
            SELECT TO_CHAR(s.start_time AT TIME ZONE 'America/New_York', 'YYYY-MM') AS month_key,
                   COUNT(a.appt_id) FILTER (WHERE a.status = 'Completed') AS completed,
                   COUNT(a.appt_id) FILTER (WHERE a.status = 'Rescheduled' OR a.reschedule_count > 0) AS rescheduled,
                   COUNT(a.appt_id) FILTER (WHERE a.status IN ('Cancelled', 'No-show')) AS missed_or_cancelled
            FROM slots s
            JOIN appointments a ON a.slot_id = s.slot_id
            WHERE s.start_time >= NOW() - INTERVAL '180 days'
            GROUP BY month_key
            ORDER BY month_key ASC;
            """
        )
        monthly_trend = cur.fetchall()

        # Consent compliance summary (BR-18, BR-19, BR-24)
        cur.execute(
            """
            SELECT
              COUNT(*) AS total_patients,
              COUNT(*) FILTER (WHERE consent_c1_version = 'v1.0') AS c1_granted,
              COUNT(*) FILTER (WHERE consent_c2_reminders = TRUE) AS c2_opted_in
            FROM patients;
            """
        )
        consent_stats = cur.fetchone()

        # Immutable audit trail (BR-16 — zero PHI/PII)
        cur.execute(
            """
            SELECT audit_id, actor_role, actor_alias, action, appt_id,
                   old_slot_id, new_slot_id, detail, idempotency_prefix, created_at
            FROM audit_logs
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 40;
            """
        )
        audits = cur.fetchall()

    total_slots = max(1, agg["total_slots"])
    total_appts = max(1, agg["booked_slots"])
    util_pct = round((agg["booked_slots"] / total_slots) * 100, 1)
    resched_pct = round((agg["rescheduled_count"] / total_appts) * 100, 1)
    late_cancel_pct = round((agg["late_cancel_count"] / total_appts) * 100, 1)
    noshow_pct = round((agg["noshow_count"] / total_appts) * 100, 1)

    provider_cards = []
    for p in providers:
        p_util = round((p["booked_slots"] / max(1, p["total_slots"])) * 100, 1)
        provider_cards.append({
            "provider_id": p["provider_id"],
            "display_name": p["display_name"],
            "specialty": p["specialty"],
            "location_name": p["location_name"],
            "utilization_pct": p_util,
            "flag": classify_utilization(p_util),
            "active_delay_minutes": p["active_delay_minutes"],
        })

    serialized_audits = []
    for a in audits:
        row = dict(a)
        row["created_at"] = _serialize_dt(a["created_at"])
        serialized_audits.append(row)

    return safe_json({
        "window": window,
        "summary": {
            "utilization_pct": util_pct,
            "total_booked": agg["booked_slots"],
            "completed_count": agg["completed_count"],
            "reschedule_rate_pct": resched_pct,
            "rescheduled_count": agg["rescheduled_count"],
            "late_cancel_rate_pct": late_cancel_pct,
            "late_cancel_count": agg["late_cancel_count"],
            "noshow_rate_pct": noshow_pct,
            "noshow_count": agg["noshow_count"],
            "c1_compliance_pct": round((consent_stats["c1_granted"] / max(1, consent_stats["total_patients"])) * 100, 1),
            "c2_optin_pct": round((consent_stats["c2_opted_in"] / max(1, consent_stats["total_patients"])) * 100, 1),
        },
        "providers": provider_cards,
        "monthly_trend": monthly_trend,
        "audit_logs": serialized_audits,
    })


# --- Static Portal & Legacy /patient/ Alias ---
if STATIC_PORTAL_DIR.exists():
    app.mount("/static/portal", StaticFiles(directory=str(STATIC_PORTAL_DIR)), name="portal")


@app.get("/")
@app.get("/patient/")
@app.get("/patient")
def serve_portal():
    index_file = STATIC_PORTAL_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Portal assets missing.")
    return FileResponse(str(index_file), media_type="text/html")
