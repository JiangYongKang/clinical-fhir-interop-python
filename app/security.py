"""Role/purpose based access control and export masking policy.

Access matrix (action x role)
-----------------------------
============  ==========  ==========  =======
action        clinician   researcher  admin
============  ==========  ==========  =======
read          yes         yes         yes
write         yes         no          yes
export        yes         yes         no
history       yes         no          yes
============  ==========  ==========  =======

Denied access raises ``Rejected(FORBIDDEN)`` which the service layer keeps
distinct from ``NOT_FOUND`` so callers can tell "exists but not allowed"
apart from "does not exist".

Masking policy
--------------
Sensitive fields (patient name parts, MRN, birth date) are masked on export
unless the actor is a clinician acting for treatment. Masking replaces the
value with a fixed placeholder -- the raw value never appears in the export
or in the audit trail.
"""

from __future__ import annotations

from enum import Enum

from .errors import Rejected, RejectReason
from .models import ResourceType

MASK = "***"


class Role(str, Enum):
    CLINICIAN = "clinician"
    RESEARCHER = "researcher"
    ADMIN = "admin"


class Purpose(str, Enum):
    TREATMENT = "treatment"
    RESEARCH = "research"
    BILLING = "billing"


class Actor:
    """An authenticated caller with a role and a declared purpose of use."""

    def __init__(self, subject: str, role: Role, purpose: Purpose) -> None:
        self.subject = subject
        self.role = Role(role)
        self.purpose = Purpose(purpose)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Actor({self.subject!r}, {self.role.value}, {self.purpose.value})"


_ACCESS: dict[str, frozenset[Role]] = {
    "read": frozenset({Role.CLINICIAN, Role.RESEARCHER, Role.ADMIN}),
    "write": frozenset({Role.CLINICIAN, Role.ADMIN}),
    "export": frozenset({Role.CLINICIAN, Role.RESEARCHER}),
    "history": frozenset({Role.CLINICIAN, Role.ADMIN}),
}

# Fields considered sensitive per resource type.
_SENSITIVE: dict[ResourceType, tuple[str, ...]] = {
    ResourceType.PATIENT: ("family_name", "given_name", "birth_date", "mrn"),
    ResourceType.OBSERVATION: (),
}


def check_access(actor: Actor, action: str) -> None:
    """Raise Rejected(FORBIDDEN) when the actor may not perform ``action``."""
    allowed = _ACCESS.get(action, frozenset())
    if actor.role not in allowed:
        raise Rejected(
            RejectReason.FORBIDDEN,
            f"role {actor.role.value!r} may not perform {action!r}",
        )


def _may_see_sensitive(actor: Actor) -> bool:
    return actor.role == Role.CLINICIAN and actor.purpose == Purpose.TREATMENT


def mask_payload(actor: Actor, resource_type: ResourceType, payload: dict) -> tuple[dict, list[str]]:
    """Return a masked copy of ``payload`` plus the list of masked field names."""
    if _may_see_sensitive(actor):
        return dict(payload), []
    masked = dict(payload)
    hidden: list[str] = []
    for field_name in _SENSITIVE.get(resource_type, ()):
        if field_name in masked and masked[field_name] not in (None, ""):
            masked[field_name] = MASK
            hidden.append(field_name)
    return masked, hidden
