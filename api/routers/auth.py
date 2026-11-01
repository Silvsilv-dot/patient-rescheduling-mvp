"""Patient sign-in (BR-15, BR-20)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from api.deps import Now
from api.errors import ApiError
from api.schemas import SignInRequest, TokenResponse
from api.security import TOKEN_TTL_SECONDS, auth_rate_limiter, create_token
from db.connection import get_conn
from rules.business_rules import Role
from services import auth_service

router = APIRouter(prefix="/api/v1", tags=["sign-in"])


@router.post("/auth/patient", response_model=TokenResponse)
def sign_in_patient(body: SignInRequest, request: Request, now: Now) -> TokenResponse:
    """Exchange an appointment ID and access code for a short-lived token.

    Wrong codes and unknown IDs return the same message. Five wrong codes lock
    that appointment's sign-in for five minutes (HTTP 429).
    """
    client = request.client.host if request.client else "unknown"
    if not auth_rate_limiter.allow(client):
        raise ApiError(429, "rate_limited")

    with get_conn() as conn:
        result = auth_service.authenticate_patient(conn, body.appt_id, body.access_code, now)
    # Read the result only after the block, so a failed attempt is saved first.
    if result.locked:
        raise ApiError(429, "locked")
    if not result.ok or result.appt_id is None or result.patient_id is None:
        raise ApiError(401, "invalid_credentials")

    token = create_token(Role.PATIENT.value, result.appt_id, now, patient_id=result.patient_id)
    return TokenResponse(access_token=token, expires_in=TOKEN_TTL_SECONDS, appt_id=result.appt_id)
