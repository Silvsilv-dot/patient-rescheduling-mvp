"""Privacy notice and consent (BR-18, BR-19)."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import CurrentPatient, Now
from api.errors import ApiError
from api.schemas import ConsentOut, ConsentRequest, ConsentSummary, NoticeOut
from db.connection import get_conn
from services import consent_service

router = APIRouter(prefix="/api/v1", tags=["privacy and consent"])


@router.get("/privacy-notice", response_model=NoticeOut)
def privacy_notice() -> NoticeOut:
    """The current privacy notice. Public, so it can be shown before sign-in."""
    with get_conn() as conn:
        notice = consent_service.get_notice(conn)
    if notice is None:
        raise ApiError(503, "unavailable")
    return NoticeOut(
        version=notice["version"],
        text=notice["notice_text"],
        effective_from=notice["effective_from"],
    )


@router.post("/consents", response_model=ConsentOut, status_code=201)
def record_consent(body: ConsentRequest, patient: CurrentPatient, now: Now) -> ConsentOut:
    """Record the patient's choice: C1 (privacy notice) or C2 (reminders)."""
    with get_conn() as conn:
        saved = consent_service.record_consent(
            conn,
            patient_id=patient.patient_id,
            consent_type=body.consent_type,
            status=body.status,
            notice_version=body.notice_version,
            now=now,
        )
    return ConsentOut(**saved)


@router.get("/consents/me", response_model=ConsentSummary)
def my_consents(patient: CurrentPatient) -> ConsentSummary:
    """Where the signed-in patient stands on each consent."""
    with get_conn() as conn:
        summary = consent_service.summary(conn, patient.patient_id)
    return ConsentSummary(**summary)
