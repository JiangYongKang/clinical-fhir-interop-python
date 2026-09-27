"""Malformed input and normalization: rejections must be classifiable."""

import pytest

from app.errors import Rejected, RejectReason

from .conftest import OBSERVATION_OK, PATIENT_OK


def _write(service, actor, rtype, payload, key="k"):
    return service.write_resource(actor, rtype, payload, base_version=None, idempotency_key=key)


def test_missing_required_field_rejected(service, clinician, log):
    payload = {"id": "p1", "family": "Wang"}  # no given name / birth date
    log.info("input=%s actor=%s", payload, clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, "Patient", payload)
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.MISSING_FIELD


def test_unknown_code_rejected(service, clinician, log):
    payload = dict(OBSERVATION_OK, coding={"system": "loinc", "code": "9999-9"})
    log.info("input=%s actor=%s", payload, clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, "Observation", payload)
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.INVALID_CODING


def test_unknown_coding_system_rejected(service, clinician, log):
    payload = dict(OBSERVATION_OK, coding={"system": "local-xyz", "code": "1"})
    log.info("input=%s actor=%s", payload, clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, "Observation", payload)
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.INVALID_CODING


def test_unparseable_time_rejected(service, clinician, log):
    payload = dict(OBSERVATION_OK, effectiveDateTime="not-a-time")
    log.info("input=%s actor=%s", payload, clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, "Observation", payload)
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.INVALID_TIME


def test_unsupported_resource_type_rejected(service, clinician, log):
    log.info("input=Medication actor=%s", clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, "Medication", PATIENT_OK)
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.UNSUPPORTED_RESOURCE


def test_heterogeneous_source_normalized(service, clinician, log):
    """SNOMED alias + offset time + unit alias map to canonical form."""
    log.info("input=%s actor=%s", OBSERVATION_OK, clinician)
    out = _write(service, clinician, "Observation", OBSERVATION_OK)
    log.info("normalized=%s", out["payload"])
    assert out["payload"]["code"]["system"] == "loinc"
    assert out["payload"]["code"]["code"] == "8480-6"
    assert out["payload"]["effective_time"] == "2026-09-27T02:00:00Z"
    assert out["payload"]["unit"] == "mm[Hg]"


def test_bare_date_becomes_utc_midnight(service, clinician, log):
    payload = dict(PATIENT_OK, birthDate="1980-05-01")
    log.info("input=%s actor=%s", payload, clinician)
    out = _write(service, clinician, "Patient", payload)
    log.info("normalized birth_date=%s", out["payload"]["birth_date"])
    assert out["payload"]["birth_date"] == "1980-05-01"


def test_denials_are_audited_with_reason(service, clinician, log):
    with pytest.raises(Rejected):
        _write(service, clinician, "Patient", {"id": "p9"})
    events = service._audit.events()
    deny = [e for e in events if e.decision == "deny"]
    log.info("audit deny events=%s", deny)
    assert deny and deny[-1].reason == RejectReason.MISSING_FIELD.value
