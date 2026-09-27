"""Local versioned storage with optimistic concurrency and idempotency.

Concurrency semantics
---------------------
Every write must present the version the writer based its change on
(``base_version``). For a new resource the base version is ``None`` (or 0).
If the stored version differs, the write is rejected with
``VERSION_CONFLICT`` -- no silent last-writer-wins overwrite.

Idempotency
-----------
Each mutation carries a client-supplied ``idempotency_key``. A key that was
already applied returns the recorded outcome unchanged (stable replay); the
same key with a *different* payload is rejected as a conflict.

History
-------
Every applied version is appended to a per-resource history so prior states
remain traceable.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

from .errors import Rejected, RejectReason
from .models import Observation, Patient, ResourceType, VersionedResource


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[ResourceType, str], VersionedResource] = {}
        # idempotency key -> (resource key, base_version, resulting version, payload)
        self._idempotency: dict[str, tuple[tuple[ResourceType, str], int | None, int, Patient | Observation]] = {}

    def get(self, resource_type: ResourceType, resource_id: str) -> VersionedResource:
        key = (resource_type, resource_id)
        with self._lock:
            if key not in self._data:
                raise Rejected(
                    RejectReason.NOT_FOUND,
                    f"{resource_type.value}/{resource_id} does not exist",
                )
            return self._data[key]

    def put(
        self,
        resource_type: ResourceType,
        resource_id: str,
        payload: Patient | Observation,
        *,
        base_version: int | None,
        idempotency_key: str,
        actor: str,
    ) -> VersionedResource:
        if not idempotency_key:
            raise Rejected(RejectReason.MISSING_FIELD, "idempotency_key is required")
        key = (resource_type, resource_id)
        with self._lock:
            # --- idempotent replay -------------------------------------
            prior = self._idempotency.get(idempotency_key)
            if prior is not None:
                prior_key, prior_base, prior_version, prior_payload = prior
                if prior_key != key or prior_base != base_version or prior_payload != payload:
                    raise Rejected(
                        RejectReason.VERSION_CONFLICT,
                        f"idempotency key {idempotency_key!r} already used with a different request",
                    )
                return self._data[key]  # stable replay of the original outcome

            current = self._data.get(key)
            current_version = current.version if current else 0
            expected = base_version or 0
            if expected != current_version:
                raise Rejected(
                    RejectReason.VERSION_CONFLICT,
                    f"base_version {expected} does not match current version {current_version}",
                )

            new_version = current_version + 1
            history = list(current.history) if current else []
            if current:
                history.append(current.payload)
            record = VersionedResource(
                resource_type=resource_type,
                resource_id=resource_id,
                version=new_version,
                payload=payload,
                updated_at=_now(),
                updated_by=actor,
                history=history,
            )
            self._data[key] = record
            self._idempotency[idempotency_key] = (key, base_version, new_version, payload)
            return record

    def history(self, resource_type: ResourceType, resource_id: str) -> list[VersionedResource]:
        """Return all versions, oldest first, including the current one."""
        current = self.get(resource_type, resource_id)
        out: list[VersionedResource] = []
        for i, payload in enumerate(current.history, start=1):
            out.append(replace(current, version=i, payload=payload))
        out.append(current)
        return out
