"""Typed rejection reasons for the clinical interop service.

Every rejection carries a machine-readable ``reason`` so that callers can
distinguish malformed input, unknown coding, version conflicts, idempotent
replays, missing resources and authorization failures.
"""

from __future__ import annotations

from enum import Enum


class RejectReason(str, Enum):
    # Ingestion / normalization failures
    MISSING_FIELD = "missing_field"
    INVALID_CODING = "invalid_coding"
    INVALID_TIME = "invalid_time"
    UNSUPPORTED_RESOURCE = "unsupported_resource"
    # Concurrency / versioning
    VERSION_CONFLICT = "version_conflict"
    # Lookup / authorization
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"


class Rejected(Exception):
    """Raised whenever an operation is refused with a classifiable reason."""

    def __init__(self, reason: RejectReason, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason.value}: {detail}")
