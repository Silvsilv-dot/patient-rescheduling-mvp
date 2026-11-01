"""Request and response models (BR-03, BR-21).

Response models are the only things that leave the API, so a field not listed
here can never leak. ``tests`` compare these fields with the whitelists in
``rules/business_rules.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class RequestModel(BaseModel):
    """Requests reject unknown fields."""

    model_config = ConfigDict(extra="forbid")


# ---- sign-in -------------------------------------------------------------
class SignInRequest(RequestModel):
    appt_id: str = Field(max_length=64)
    access_code: str = Field(max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    appt_id: str


# ---- notice and consent --------------------------------------------------
class NoticeOut(BaseModel):
    version: str
    text: str
    effective_from: datetime


class ConsentRequest(RequestModel):
    consent_type: Literal["C1", "C2"]
    status: Literal["Granted", "Declined", "Withdrawn"]
    notice_version: str = Field(min_length=1, max_length=16)


class ConsentOut(BaseModel):
    consent_type: str
    status: str
    notice_version: str
    recorded_at: datetime


class ConsentState(BaseModel):
    status: Optional[str]
    notice_version: Optional[str]
    satisfied: bool


class ConsentSummary(BaseModel):
    notice_version_current: str
    c1: ConsentState
    c2: ConsentState


# ---- appointment ---------------------------------------------------------
class ActionAvailability(BaseModel):
    allowed: bool
    message: Optional[str]


class AppointmentOut(BaseModel):
    """BR-03: date and time, time zone, location, generic label, status (plus ID and actions)."""

    appt_id: str
    start_time: datetime
    time_zone: str
    location: str
    label: str
    status: str
    allowed_actions: dict[str, ActionAvailability]


class CancelRequest(RequestModel):
    # Strict: only a real true/false counts. Text such as "yes" or 1 must not cancel anything.
    confirm: StrictBool


# ---- slots and waits -----------------------------------------------------
class SlotOut(BaseModel):
    slot_id: str
    provider: str
    location: str
    start_time: datetime
    selectable: bool
    reason: Optional[str]


class SlotsOut(BaseModel):
    slots: list[SlotOut]
    available_count: int
    needs_fallback: bool
    message: Optional[str]


class WaitEstimateOut(BaseModel):
    provider: str
    location: str
    earliest_start: datetime
    wait_label: str
    is_soonest: bool


class WaitTypeOut(BaseModel):
    type_id: str
    label: str
    providers: list[WaitEstimateOut]


class WaitTimesOut(BaseModel):
    estimate_note: str
    types: list[WaitTypeOut]


# ---- reschedule and reminders -------------------------------------------
class RescheduleRequest(RequestModel):
    new_slot_id: str = Field(min_length=1, max_length=32)


class RescheduleOut(BaseModel):
    appt_id: str
    status: str
    old_start_time: datetime
    new_start_time: datetime
    reschedule_count: int
    provider: str
    location: str
    label: str


class ReminderOut(BaseModel):
    due_at: datetime
    message: str
    status: str


class RemindersOut(BaseModel):
    reminders_enabled: bool
    reminders: list[ReminderOut]


# ---- system --------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    database: str
