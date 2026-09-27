"""Normalization of inbound clinical resources.

Maps heterogeneous source payloads onto the canonical models: validates
required fields, translates coding systems, and unifies time semantics to
UTC. Anything that cannot be normalized is rejected with a typed reason.

Coding mapping rules
--------------------
Sources may send codes under several aliases. Each alias is mapped onto one
of the canonical systems (``loinc``, ``snomed``, ``icd10``). Codes that are
unknown to the local dictionary are rejected as ``INVALID_CODING`` rather
than silently persisted.

Time semantics
--------------
Timestamps may arrive as ``dateTime`` with offset, as naive local time with
a separate ``timezone`` hint, or as a bare date. All are converted to a UTC
ISO-8601 instant. Bare dates become midnight UTC. Unparseable values are
rejected as ``INVALID_TIME``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .errors import Rejected, RejectReason
from .models import CANONICAL_SYSTEMS, Coding, Observation, Patient

# --- coding system aliases -> canonical system name -----------------------
_SYSTEM_ALIASES: dict[str, str] = {
    "loinc": "loinc",
    "http://loinc.org": "loinc",
    "ln": "loinc",
    "snomed": "snomed",
    "snomed-ct": "snomed",
    "http://snomed.info/sct": "snomed",
    "sct": "snomed",
    "icd10": "icd10",
    "icd-10": "icd10",
    "http://hl7.org/fhir/sid/icd-10": "icd10",
}

# --- minimal local code dictionary (canonical system -> known codes) ------
_KNOWN_CODES: dict[str, dict[str, str]] = {
    "loinc": {
        "8480-6": "Systolic blood pressure",
        "8462-4": "Diastolic blood pressure",
        "2339-0": "Glucose",
    },
    "snomed": {
        "271649006": "Systolic blood pressure",
        "271650006": "Diastolic blood pressure",
    },
    "icd10": {
        "E11": "Type 2 diabetes mellitus",
        "I10": "Essential hypertension",
    },
}

# Cross-system equivalences: source code -> canonical (system, code).
# Observations are normalized to LOINC when an equivalent exists.
_EQUIVALENTS: dict[tuple[str, str], tuple[str, str]] = {
    ("snomed", "271649006"): ("loinc", "8480-6"),
    ("snomed", "271650006"): ("loinc", "8462-4"),
}

_UNIT_ALIASES: dict[str, str] = {
    "mmhg": "mm[Hg]",
    "mm[hg]": "mm[Hg]",
    "mg/dl": "mg/dL",
    "mgdl": "mg/dL",
}


def _require(raw: dict, *names: str) -> Any:
    for name in names:
        value = raw.get(name)
        if value is not None and value != "":
            return value
    raise Rejected(
        RejectReason.MISSING_FIELD,
        f"none of the required fields {names} present in payload",
    )


def normalize_coding(raw: Any) -> Coding:
    if not isinstance(raw, dict):
        raise Rejected(RejectReason.INVALID_CODING, "coding must be an object")
    system_raw = raw.get("system")
    code = raw.get("code")
    if not system_raw or not code:
        raise Rejected(RejectReason.MISSING_FIELD, "coding requires system and code")
    system = _SYSTEM_ALIASES.get(str(system_raw).strip().lower())
    if system is None:
        raise Rejected(
            RejectReason.INVALID_CODING,
            f"unknown coding system alias: {system_raw!r}",
        )
    code = str(code).strip()
    display = _KNOWN_CODES[system].get(code)
    if display is None:
        raise Rejected(
            RejectReason.INVALID_CODING,
            f"code {code!r} not in local dictionary for system {system!r}",
        )
    canonical = _EQUIVALENTS.get((system, code))
    if canonical is not None:
        system, code = canonical
        display = _KNOWN_CODES[system][code]
    return Coding(system=system, code=code, display=str(raw.get("display") or display))


def normalize_time(raw: Any, tz_hint: Any = None) -> str:
    """Normalize a timestamp to a UTC ISO-8601 instant."""
    if raw is None or raw == "":
        raise Rejected(RejectReason.MISSING_FIELD, "timestamp is required")
    text = str(raw).strip()
    parsed: datetime
    try:
        if len(text) == 10:  # bare date -> midnight UTC
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Rejected(RejectReason.INVALID_TIME, f"unparseable timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        if tz_hint in (None, "", "UTC", "utc", "Z"):
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            try:
                hint = str(tz_hint).strip()
                offset = datetime.fromisoformat(f"2000-01-01T00:00:00{hint.replace('Z', '+00:00')}").utcoffset()
            except ValueError as exc:
                raise Rejected(
                    RejectReason.INVALID_TIME, f"unparseable timezone hint: {tz_hint!r}"
                ) from exc
            if offset is None:
                raise Rejected(RejectReason.INVALID_TIME, f"unparseable timezone hint: {tz_hint!r}")
            parsed = parsed.replace(tzinfo=timezone.utc) - offset
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_unit(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    unit = _UNIT_ALIASES.get(str(raw).strip().lower())
    if unit is None:
        raise Rejected(RejectReason.INVALID_CODING, f"unknown unit: {raw!r}")
    return unit


def normalize_patient(raw: dict) -> Patient:
    """Validate and normalize an inbound Patient payload."""
    if not isinstance(raw, dict):
        raise Rejected(RejectReason.MISSING_FIELD, "patient payload must be an object")
    resource_id = str(_require(raw, "id", "resource_id", "identifier"))
    birth_raw = _require(raw, "birth_date", "birthDate", "dob")
    birth_date = normalize_time(birth_raw)[:10]
    return Patient(
        resource_id=resource_id,
        family_name=str(_require(raw, "family_name", "family", "lastName")),
        given_name=str(_require(raw, "given_name", "given", "firstName")),
        birth_date=birth_date,
        mrn=str(raw.get("mrn") or raw.get("medical_record_number") or ""),
    )


def normalize_observation(raw: dict) -> Observation:
    """Validate and normalize an inbound Observation payload."""
    if not isinstance(raw, dict):
        raise Rejected(RejectReason.MISSING_FIELD, "observation payload must be an object")
    resource_id = str(_require(raw, "id", "resource_id", "identifier"))
    patient_id = str(_require(raw, "patient_id", "subject", "patient"))
    code = normalize_coding(_require(raw, "code", "coding"))
    effective = normalize_time(
        _require(raw, "effective_time", "effectiveDateTime", "effective"),
        raw.get("timezone"),
    )
    value = raw.get("value")
    if value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise Rejected(RejectReason.MISSING_FIELD, f"value not numeric: {value!r}") from exc
    return Observation(
        resource_id=resource_id,
        patient_id=patient_id,
        code=code,
        value=value,
        unit=normalize_unit(raw.get("unit")),
        effective_time=effective,
    )


__all__ = [
    "CANONICAL_SYSTEMS",
    "normalize_patient",
    "normalize_observation",
    "normalize_coding",
    "normalize_time",
]
