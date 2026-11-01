"""One standard error format for every failure (BR-22).

Bodies look like ``{"error": {"code": "slot_taken", "message": "..."}}`` and never
contain stack traces, SQL, file paths, connection details or the user's input.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config.settings import ConfigError
from db.connection import DatabaseUnavailableError
from rules.business_rules import MESSAGES, RuleResult, error_body
from services.exceptions import ConflictError, NotFoundError, RuleDenied

logger = logging.getLogger(__name__)

API_MESSAGES: dict[str, str] = {
    **MESSAGES,
    "invalid_request": "Some of the information was not valid. Please check and try again.",
    "invalid_credentials": MESSAGES["not_found"],
    "unauthorized": "Please sign in again.",
    "internal": "Something went wrong. Please try again.",
    "rate_limited": "Too many requests. Please wait a moment and try again.",
    "idempotency_conflict": "That request key was already used for a different request.",
    "notice_outdated": "The privacy notice has changed. Please review it again.",
    "payload_too_large": "That request is too large.",
    "method_not_allowed": "That action is not available here.",
}


class ApiError(Exception):
    """Raise inside a route to return a standard error response."""

    def __init__(self, status_code: int, code: str, message: Optional[str] = None) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message or API_MESSAGES.get(code, API_MESSAGES["internal"])


def error_response(status_code: int, code: str, message: Optional[str] = None) -> JSONResponse:
    """Build a standard error response."""
    text = message or API_MESSAGES.get(code, API_MESSAGES["internal"])
    return JSONResponse(status_code=status_code, content=error_body(code, text))


def rule_denial_response(result: RuleResult) -> JSONResponse:
    """Turn a business-rule refusal into HTTP 409 (a taken slot is reported as slot_taken)."""
    code = "slot_taken" if result.code == "slot_unavailable" else (result.code or "internal")
    return error_response(409, code)


_STATUS_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
}


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers so every error uses the standard format."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RuleDenied)
    async def _rule_denied(_: Request, exc: RuleDenied) -> JSONResponse:
        return rule_denial_response(exc.result)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return error_response(409, exc.code)

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return error_response(404, "not_found")

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Details are not returned: they could echo what the user typed.
        return error_response(422, "invalid_request")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "internal")
        return error_response(exc.status_code, code)

    @app.exception_handler(DatabaseUnavailableError)
    @app.exception_handler(ConfigError)
    async def _unavailable(_: Request, exc: Exception) -> JSONResponse:
        logger.warning("Service unavailable: %s", type(exc).__name__)
        return error_response(503, "unavailable")
