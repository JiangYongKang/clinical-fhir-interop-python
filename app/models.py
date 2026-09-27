"""Canonical internal representation of clinical resources.

External payloads (possibly from heterogeneous sources) are normalized into
these models before they are stored. All timestamps are UTC ISO-8601 and all
codes use the canonical coding systems declared in ``CANONICAL_SYSTEMS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ResourceType(str, Enum):
    PATIENT = "Patient"
    OBSERVATION = "Observation"


CANONICAL_SYSTEMS = ("loinc", "snomed", "icd10")


@dataclass(frozen=True)
class Coding:
    system: str
    code: str
    display: str = ""


@dataclass(frozen=True)
class Patient:
    resource_id: str
    family_name: str
    given_name: str
    birth_date: str  # ISO-8601 date, UTC semantics
    mrn: str = ""    # medical record number (sensitive)


@dataclass(frozen=True)
class Observation:
    resource_id: str
    patient_id: str
    code: Coding
    value: float | None
    unit: str
    effective_time: str  # UTC ISO-8601 instant


@dataclass
class VersionedResource:
    """A stored resource together with its version metadata."""

    resource_type: ResourceType
    resource_id: str
    version: int
    payload: Patient | Observation
    updated_at: str
    updated_by: str
    history: list[Patient | Observation] = field(default_factory=list)
