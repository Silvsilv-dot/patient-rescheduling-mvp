"""Audit log writes (BR-16). Roles and actions are validated by the rules module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from rules.business_rules import audit_entry


def record(
    conn: Any,
    role: str,
    action: str,
    appt_id: Optional[str],
    now: datetime,
) -> None:
    """Write one audit entry inside the caller's transaction."""
    entry = audit_entry(role, action, appt_id)
    conn.execute(
        "INSERT INTO audit_log (actor_role, action, appt_id, created_at) VALUES (%s, %s, %s, %s)",
        (entry.actor_role, entry.action, entry.appt_id, now),
    )


def list_recent(conn: Any, limit: int = 100) -> list[dict[str, Any]]:
    """Most recent audit entries, newest first (used by the manager view later)."""
    return conn.execute(
        "SELECT audit_id, actor_role, action, appt_id, created_at "
        "FROM audit_log ORDER BY created_at DESC, audit_id DESC LIMIT %s",
        (limit,),
    ).fetchall()
