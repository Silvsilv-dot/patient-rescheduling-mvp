"""Shared route dependencies: clock, sign-in check, role check, consent gate (BR-15, BR-18)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.errors import ApiError
from api.security import TokenClaims, decode_token
from config.settings import OFFICE_TZ
from db.connection import get_conn
from rules.business_rules import AuditAction, Role, is_allowed
from services import audit_service, consent_service

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def get_now() -> datetime:
    """The current time in the office time zone (tests replace this)."""
    return datetime.now(ZoneInfo(OFFICE_TZ))


Now = Annotated[datetime, Depends(get_now)]
ApptId = Annotated[str, Path(max_length=64)]  # appointment IDs are short; refuse huge ones


def get_claims(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
    now: Now,
) -> TokenClaims:
    """Require a valid, unexpired token."""
    if credentials is None:
        raise ApiError(401, "unauthorized")
    return decode_token(credentials.credentials, now)


Claims = Annotated[TokenClaims, Depends(get_claims)]


@dataclass(frozen=True)
class PatientContext:
    """A signed-in patient and the one appointment their token covers."""

    patient_id: str
    appt_id: str


def _deny(claims: TokenClaims, now: datetime, appt_id: Optional[str]) -> ApiError:
    """Log a refused access (best effort) and return the 403."""
    try:
        with get_conn() as conn:
            audit_service.record(conn, claims.role, AuditAction.ACCESS_DENIED.value, appt_id, now)
    except Exception as exc:  # the refusal must not fail because logging failed
        logger.warning("Could not write audit entry: %s", type(exc).__name__)
    return ApiError(403, "forbidden")


def current_patient(claims: Claims, now: Now) -> PatientContext:
    """A signed-in patient (BR-15)."""
    if (
        claims.role != Role.PATIENT.value
        or not claims.patient_id
        or not is_allowed(claims.role, "appointment:read_own")
    ):
        raise _deny(claims, now, None)
    return PatientContext(patient_id=claims.patient_id, appt_id=claims.subject)


def patient_for_appointment(
    appt_id: ApptId,
    patient: Annotated[PatientContext, Depends(current_patient)],
    claims: Claims,
    now: Now,
) -> PatientContext:
    """The patient may only reach the appointment their token covers (BR-15)."""
    if patient.appt_id != appt_id:
        # Log the caller's own appointment, never the text they asked for.
        raise _deny(claims, now, patient.appt_id)
    return patient


def _require_c1(patient: PatientContext) -> PatientContext:
    with get_conn() as conn:
        satisfied = consent_service.is_c1_satisfied(conn, patient.patient_id)
    if not satisfied:
        raise ApiError(403, "consent_required")
    return patient


def patient_with_consent(
    patient: Annotated[PatientContext, Depends(current_patient)],
) -> PatientContext:
    """A signed-in patient who has accepted the current notice (BR-18)."""
    return _require_c1(patient)


def appointment_with_consent(
    patient: Annotated[PatientContext, Depends(patient_for_appointment)],
) -> PatientContext:
    """Same, for routes about one appointment (BR-15 and BR-18)."""
    return _require_c1(patient)


CurrentPatient = Annotated[PatientContext, Depends(current_patient)]
PatientWithConsent = Annotated[PatientContext, Depends(patient_with_consent)]
AppointmentAccess = Annotated[PatientContext, Depends(appointment_with_consent)]
