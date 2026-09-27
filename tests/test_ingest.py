"""接入与归一：畸形输入必须被可区分地拒绝，合法输入被统一映射。"""
import pytest

from app.errors import InteropError, RejectReason
from tests.conftest import log_case

OBS = {
    "resourceType": "Observation",
    "id": "obs-1",
    "code": {"system": "local", "code": "HR"},
    "effectiveDateTime": "2026-09-27T10:00:00+08:00",
    "value": 72,
}


def test_valid_observation_normalized(svc, clinician):
    log_case("valid-obs", inputs=OBS, parties=[clinician.subject],
             rationale="本地码 HR 应映射到 LOINC 8867-4，+08:00 时间应归一为 UTC")
    out = svc.ingest(clinician, OBS, source="device-A")
    assert out["created"] is True and out["version"] == 1
    assert out["normalized"]["code"]["system"] == "http://loinc.org"
    assert out["normalized"]["code"]["code"] == "8867-4"
    assert out["normalized"]["effective_time"] == "2026-09-27T02:00:00+00:00"


def test_missing_id_rejected(svc, clinician):
    bad = {k: v for k, v in OBS.items() if k != "id"}
    log_case("missing-id", inputs=bad, parties=[clinician.subject],
             rationale="缺少 id，须以 missing_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.MISSING_FIELD


def test_missing_code_rejected(svc, clinician):
    bad = {k: v for k, v in OBS.items() if k != "code"}
    log_case("missing-code", inputs=bad, parties=[clinician.subject],
             rationale="Observation 缺少 code，须以 missing_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.MISSING_FIELD


def test_invalid_code_rejected(svc, clinician):
    bad = dict(OBS, code={"system": "loinc", "code": "not-a-code"})
    log_case("invalid-code", inputs=bad, parties=[clinician.subject],
             rationale="LOINC 码格式非法，须以 invalid_code 拒绝且不得静默写入")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.INVALID_CODE
    assert svc.store.get_current.__self__ is not None
    from app.models import ResourceType
    assert svc.store.get_current(ResourceType.OBSERVATION, "obs-1") is None


def test_unknown_system_rejected(svc, clinician):
    bad = dict(OBS, code={"system": "unknown-sys", "code": "HR"})
    log_case("unknown-system", inputs=bad, parties=[clinician.subject],
             rationale="未识别的编码体系，须以 invalid_code 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.INVALID_CODE


def test_invalid_time_rejected(svc, clinician):
    bad = dict(OBS, effectiveDateTime="not-a-time")
    log_case("invalid-time", inputs=bad, parties=[clinician.subject],
             rationale="时间无法解析，须以 invalid_time 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.INVALID_TIME


def test_unsupported_resource_type(svc, clinician):
    bad = {"resourceType": "Medication", "id": "m1"}
    log_case("unsupported-type", inputs=bad, parties=[clinician.subject],
             rationale="不支持的资源类型，须以 unsupported_resource 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="device-A")
    assert ei.value.reason == RejectReason.UNSUPPORTED_RESOURCE


def test_patient_missing_name_rejected(svc, clinician):
    bad = {"resource_type": "Patient", "id": "p1"}
    log_case("patient-missing-name", inputs=bad, parties=[clinician.subject],
             rationale="Patient 缺少 name，须以 missing_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="his")
    assert ei.value.reason == RejectReason.MISSING_FIELD


def test_epoch_time_normalized(svc, clinician):
    obs = dict(OBS, id="obs-epoch")
    obs.pop("effectiveDateTime")
    obs["effective_time"] = 1790000000
    log_case("epoch-time", inputs=obs, parties=[clinician.subject],
             rationale="epoch 秒应归一为 UTC ISO 时间")
    out = svc.ingest(clinician, obs, source="device-B")
    assert out["normalized"]["effective_time"].endswith("+00:00")
