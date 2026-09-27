"""Duplicate submissions, version conflicts and history traceability."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.errors import Rejected, RejectReason

from .conftest import PATIENT_OK


def _write(service, actor, payload, base, key):
    return service.write_resource(actor, "Patient", payload, base_version=base, idempotency_key=key)


def test_duplicate_submission_is_stable(service, clinician, log):
    log.info("input=%s actor=%s key=dup-1", PATIENT_OK, clinician)
    first = _write(service, clinician, PATIENT_OK, None, "dup-1")
    replay = _write(service, clinician, PATIENT_OK, None, "dup-1")
    log.info("first_version=%s replay_version=%s", first["version"], replay["version"])
    assert first["version"] == replay["version"] == 1


def test_idempotency_key_reuse_with_different_payload_conflicts(service, clinician, log):
    _write(service, clinician, PATIENT_OK, None, "dup-2")
    changed = dict(PATIENT_OK, given="Changed")
    log.info("input=%s actor=%s key=dup-2 (reused)", changed, clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, changed, None, "dup-2")
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.VERSION_CONFLICT


def test_stale_base_version_rejected(service, clinician, log):
    _write(service, clinician, PATIENT_OK, None, "v-1")
    updated = dict(PATIENT_OK, given="Fang2")
    _write(service, clinician, updated, 1, "v-2")
    log.info("input base_version=1 while current=2 actor=%s", clinician)
    with pytest.raises(Rejected) as err:
        _write(service, clinician, dict(PATIENT_OK, given="Fang3"), 1, "v-3")
    log.info("decision=%s basis=%s", err.value.reason.value, err.value.detail)
    assert err.value.reason == RejectReason.VERSION_CONFLICT


def test_concurrent_writes_no_lost_update(service, clinician, log):
    _write(service, clinician, PATIENT_OK, None, "seed")
    log.info("spawning 8 concurrent writers, all based on version=1")

    def attempt(i):
        try:
            _write(service, clinician, dict(PATIENT_OK, given=f"W{i}"), 1, f"race-{i}")
            return "ok"
        except Rejected as exc:
            return exc.reason.value

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(8)))
    log.info("results=%s participants=8 basis=optimistic version check", results)
    assert results.count("ok") == 1
    assert results.count(RejectReason.VERSION_CONFLICT.value) == 7
    final = service.read_resource(clinician, "Patient", "p1")
    assert final["version"] == 2  # exactly one update applied


def test_history_is_traceable(service, clinician, log):
    _write(service, clinician, PATIENT_OK, None, "h-1")
    _write(service, clinician, dict(PATIENT_OK, given="Fang2"), 1, "h-2")
    _write(service, clinician, dict(PATIENT_OK, given="Fang3"), 2, "h-3")
    history = service.resource_history(clinician, "Patient", "p1")
    log.info("history=%s", [(h["version"], h["payload"]["given_name"]) for h in history])
    assert [h["version"] for h in history] == [1, 2, 3]
    assert [h["payload"]["given_name"] for h in history] == ["Fang", "Fang2", "Fang3"]
