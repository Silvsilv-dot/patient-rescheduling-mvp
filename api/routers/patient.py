"""The patient flow: view, confirm, cancel, slots, wait times, reschedule, reminders."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Header, Query, Response

from api.deps import AppointmentAccess, ApptId, Now, PatientWithConsent
from api.errors import ApiError
from api.schemas import (
    AppointmentOut,
    CancelRequest,
    RemindersOut,
    RescheduleOut,
    RescheduleRequest,
    SlotsOut,
    WaitTimesOut,
)
from db.connection import get_conn
from rules.business_rules import is_valid_idempotency_key
from services import (
    appointment_service,
    reminder_service,
    reschedule_service,
    slot_service,
    wait_time_service,
)

router = APIRouter(prefix="/api/v1", tags=["patient"])


@router.get("/appointments/{appt_id}", response_model=AppointmentOut)
def get_appointment(appt_id: ApptId, patient: AppointmentAccess, now: Now) -> dict:
    """Date, time, location, generic label and status only (BR-03)."""
    with get_conn() as conn:
        record = appointment_service.get_record(conn, appt_id)
    return appointment_service.appointment_view(record, now)


@router.post("/appointments/{appt_id}/confirm", response_model=AppointmentOut)
def confirm_appointment(appt_id: ApptId, patient: AppointmentAccess, now: Now) -> dict:
    with get_conn() as conn:
        record = appointment_service.confirm(conn, appt_id, now)
    return appointment_service.appointment_view(record, now)


@router.post("/appointments/{appt_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(
    appt_id: ApptId, body: CancelRequest, patient: AppointmentAccess, now: Now
) -> dict:
    """The body must say ``{"confirm": true}``: cancelling needs an explicit yes (BR-11)."""
    if not body.confirm:
        raise ApiError(422, "invalid_request", "Please confirm that you want to cancel.")
    with get_conn() as conn:
        record = appointment_service.cancel(conn, appt_id, now)
    return appointment_service.appointment_view(record, now)


@router.get("/appointments/{appt_id}/slots", response_model=SlotsOut)
def get_slots(appt_id: ApptId, patient: AppointmentAccess, now: Now) -> dict:
    """Same-type slots for the next 14 days, in time order; unavailable ones are disabled."""
    with get_conn() as conn:
        record = appointment_service.get_record(conn, appt_id)
        return slot_service.list_slots(conn, record, now)


@router.get("/wait-times", response_model=WaitTimesOut)
def get_wait_times(
    patient: PatientWithConsent,
    now: Now,
    type_id: Annotated[Optional[str], Query(max_length=16)] = None,
) -> dict:
    """Estimated wait per provider, for one appointment type or all types (BR-12)."""
    with get_conn() as conn:
        return wait_time_service.estimates(conn, now, type_id)


@router.post("/appointments/{appt_id}/reschedule", response_model=RescheduleOut)
def reschedule_appointment(
    appt_id: ApptId,
    body: RescheduleRequest,
    patient: AppointmentAccess,
    now: Now,
    response: Response,
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
) -> dict:
    """Move to a new slot. Send an ``Idempotency-Key`` header so a retry cannot apply twice."""
    if not is_valid_idempotency_key(idempotency_key):
        raise ApiError(400, "invalid_request", "A valid Idempotency-Key header is required.")
    assert idempotency_key is not None  # narrowed by the check above
    with get_conn() as conn:
        result, replay = reschedule_service.reschedule(
            conn, appt_id, body.new_slot_id, idempotency_key, now
        )
    if replay:
        response.headers["Idempotent-Replay"] = "true"
    return result


@router.get("/appointments/{appt_id}/reminders", response_model=RemindersOut)
def get_reminders(appt_id: ApptId, patient: AppointmentAccess) -> dict:
    """Simulated reminders, shown only if the patient opted in (BR-14, BR-19)."""
    with get_conn() as conn:
        return reminder_service.list_reminders(conn, appt_id, patient.patient_id)
