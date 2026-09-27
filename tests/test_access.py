"""Authorization, masking and audit: forbidden vs not-found must differ."""

import pytest

from app.errors import Rejected, RejectReason
from app.security import MASK

from .conftest import PATIENT_OK


def _seed(service, clinician):
    service.write_resource(clinician, "Patient", PATIENT_OK, base_version=None, idempotency_key="seed")


def test_researcher_cannot_write(service, researcher, log):
    log.info("actor=%s action=write", researcher)
    with pytest.raises(Rejected) as err:
        service.write_resource(researcher, "Patient", PATIENT_OK, base_version=None, idempotency_key="w")
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.FORBIDDEN


def test_admin_cannot_export(service, admin, clinician, log):
    _seed(service, clinician)
    log.info("actor=%s action=export", admin)
    with pytest.raises(Rejected) as err:
        service.export_resource(admin, "Patient", "p1")
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.FORBIDDEN


def test_forbidden_distinct_from_not_found(service, researcher, clinician, log):
    _seed(service, clinician)
    with pytest.raises(Rejected) as forbidden:
        service.resource_history(researcher, "Patient", "p1")  # exists, not allowed
    with pytest.raises(Rejected) as missing:
        service.read_resource(researcher, "Patient", "ghost")  # allowed, absent
    log.info("forbidden=%s not_found=%s", forbidden.value.reason, missing.value.reason)
    assert forbidden.value.reason == RejectReason.FORBIDDEN
    assert missing.value.reason == RejectReason.NOT_FOUND


def test_export_masks_sensitive_fields_for_researcher(service, researcher, clinician, log):
    _seed(service, clinician)
    out = service.export_resource(researcher, "Patient", "p1")
    log.info("actor=%s masked_fields=%s", researcher, out["masked_fields"])
    for field in ("family_name", "given_name", "birth_date", "mrn"):
        assert out["payload"][field] == MASK
    assert set(out["masked_fields"]) == {"family_name", "given_name", "birth_date", "mrn"}
    blob = repr(out)
    assert "MRN-123" not in blob and "Wang" not in blob


def test_treatment_clinician_sees_unmasked(service, clinician, log):
    _seed(service, clinician)
    out = service.export_resource(clinician, "Patient", "p1")
    log.info("actor=%s masked_fields=%s", clinician, out["masked_fields"])
    assert out["payload"]["mrn"] == "MRN-123"
    assert out["masked_fields"] == []


def test_audit_records_actor_action_and_masked_fields(service, researcher, clinician, log):
    _seed(service, clinician)
    service.export_resource(researcher, "Patient", "p1")
    events = service._audit.events()
    export_events = [e for e in events if e.action == "export"]
    log.info("audit=%s", export_events)
    assert export_events, "export must be audited"
    ev = export_events[-1]
    assert ev.actor == "lab.li" and ev.role == "researcher"
    assert ev.decision == "allow"
    assert "mrn" in ev.masked_fields
    # audit trail must never leak raw sensitive values
    blob = repr(events)
    assert "MRN-123" not in blob and "Wang" not in blob


def test_denied_access_is_audited(service, researcher, log):
    with pytest.raises(Rejected):
        service.write_resource(researcher, "Patient", PATIENT_OK, base_version=None, idempotency_key="x")
    denies = [e for e in service._audit.events() if e.decision == "deny"]
    log.info("deny events=%s", denies)
    assert denies and denies[-1].reason == RejectReason.FORBIDDEN.value
