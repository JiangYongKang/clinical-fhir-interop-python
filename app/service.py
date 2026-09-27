"""Orchestration layer: authorize -> normalize -> store -> audit.

Every operation is audited with its decision. Denials are audited too, with
the typed rejection reason, so that malformed input, conflicts and
authorization failures remain distinguishable in the trail.
"""

from __future__ import annotations

from dataclasses import asdict

from .audit import AuditLog, make_event
from .errors import Rejected, RejectReason
from .models import ResourceType
from .normalize import normalize_observation, normalize_patient
from .security import Actor, check_access, mask_payload
from .store import Store

_NORMALIZERS = {
    ResourceType.PATIENT: normalize_patient,
    ResourceType.OBSERVATION: normalize_observation,
}


class ClinicalInteropService:
    def __init__(self, store: Store, audit: AuditLog) -> None:
        self._store = store
        self._audit = audit

    # -- helpers ---------------------------------------------------------
    def _parse_type(self, resource_type: str) -> ResourceType:
        try:
            return ResourceType(resource_type)
        except ValueError as exc:
            raise Rejected(
                RejectReason.UNSUPPORTED_RESOURCE,
                f"unsupported resource type: {resource_type!r}",
            ) from exc

    def _deny(self, actor: Actor, action: str, resource: str, exc: Rejected) -> Rejected:
        self._audit.record(make_event(actor, action, resource, "deny", exc.reason.value))
        return exc

    # -- operations ------------------------------------------------------
    def write_resource(self, actor: Actor, resource_type: str, raw: dict,
                       *, base_version: int | None, idempotency_key: str) -> dict:
        resource = f"{resource_type}/?"
        try:
            rtype = self._parse_type(resource_type)
            check_access(actor, "write")
            payload = _NORMALIZERS[rtype](raw)
            resource = f"{rtype.value}/{payload.resource_id}"
            record = self._store.put(
                rtype, payload.resource_id, payload,
                base_version=base_version, idempotency_key=idempotency_key,
                actor=actor.subject,
            )
        except Rejected as exc:
            raise self._deny(actor, "write", resource, exc)
        self._audit.record(make_event(actor, "write", resource, "allow"))
        return {
            "resource_type": record.resource_type.value,
            "resource_id": record.resource_id,
            "version": record.version,
            "payload": asdict(record.payload),
        }

    def read_resource(self, actor: Actor, resource_type: str, resource_id: str) -> dict:
        resource = f"{resource_type}/{resource_id}"
        try:
            rtype = self._parse_type(resource_type)
            check_access(actor, "read")
            record = self._store.get(rtype, resource_id)
        except Rejected as exc:
            raise self._deny(actor, "read", resource, exc)
        self._audit.record(make_event(actor, "read", resource, "allow"))
        return {
            "resource_type": record.resource_type.value,
            "resource_id": record.resource_id,
            "version": record.version,
            "payload": asdict(record.payload),
        }

    def export_resource(self, actor: Actor, resource_type: str, resource_id: str) -> dict:
        resource = f"{resource_type}/{resource_id}"
        try:
            rtype = self._parse_type(resource_type)
            check_access(actor, "export")
            record = self._store.get(rtype, resource_id)
            masked, hidden = mask_payload(actor, rtype, asdict(record.payload))
        except Rejected as exc:
            raise self._deny(actor, "export", resource, exc)
        self._audit.record(make_event(actor, "export", resource, "allow", masked_fields=hidden))
        return {
            "resource_type": record.resource_type.value,
            "resource_id": record.resource_id,
            "version": record.version,
            "payload": masked,
            "masked_fields": hidden,
        }

    def resource_history(self, actor: Actor, resource_type: str, resource_id: str) -> list[dict]:
        resource = f"{resource_type}/{resource_id}"
        try:
            rtype = self._parse_type(resource_type)
            check_access(actor, "history")
            versions = self._store.history(rtype, resource_id)
        except Rejected as exc:
            raise self._deny(actor, "history", resource, exc)
        self._audit.record(make_event(actor, "history", resource, "allow"))
        return [
            {
                "resource_type": v.resource_type.value,
                "resource_id": v.resource_id,
                "version": v.version,
                "payload": asdict(v.payload),
            }
            for v in versions
        ]
