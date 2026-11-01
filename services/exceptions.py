"""Errors that services raise. The API layer turns them into HTTP responses."""

from __future__ import annotations

from rules.business_rules import RuleResult


class NotFoundError(Exception):
    """The requested record does not exist."""


class RuleDenied(Exception):
    """A business rule refused the action. ``result`` carries the code and message."""

    def __init__(self, result: RuleResult) -> None:
        super().__init__(result.code)
        self.result = result


class ConflictError(Exception):
    """The request conflicts with current state (for example a reused idempotency key)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
