"""Append-only audit log. Records actor, action, decision and masked fields.

The log must never contain raw sensitive values; only the *names* of masked
fields are stored, never their contents.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    actor: str
    role: str
    purpose: str
    action: str
    resource: str
    decision: str          # "allow" | "deny"
    reason: str = ""
    masked_fields: tuple[str, ...] = field(default_factory=tuple)


class AuditLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)


def make_event(actor, action: str, resource: str, decision: str,
               reason: str = "", masked_fields=()) -> AuditEvent:
    return AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        actor=actor.subject,
        role=actor.role.value,
        purpose=actor.purpose.value,
        action=action,
        resource=resource,
        decision=decision,
        reason=reason,
        masked_fields=tuple(masked_fields),
    )
