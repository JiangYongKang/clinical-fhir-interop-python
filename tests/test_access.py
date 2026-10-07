"""访问控制、脱敏与审计：越权可区分于不存在，导出脱敏，审计完整且不泄露敏感值。"""
import pytest

from app.errors import InteropError, RejectReason
from app.masking import MASK
from tests.conftest import log_case

PATIENT = {"resource_type": "Patient", "id": "p-1", "name": "张三",
           "phone": "13800001111", "gender": "male"}


def _seed(svc, clinician):
    svc.ingest(clinician, PATIENT, source="his")


def test_forbidden_distinct_from_not_found(svc, clinician, researcher):
    _seed(svc, clinician)
    log_case("forbidden-vs-notfound",
             inputs={"history_by": researcher.subject, "missing_id": "p-x"},
             parties=[researcher.subject, clinician.subject],
             rationale="越权须为 forbidden，不存在须为 not_found，二者可区分")
    # researcher 无 history 权限 -> forbidden（即使资源存在）
    with pytest.raises(InteropError) as ei:
        svc.history(researcher, "Patient", "p-1")
    assert ei.value.reason == RejectReason.FORBIDDEN
    # clinician 有权限但资源不存在 -> not_found
    with pytest.raises(InteropError) as ei:
        svc.history(clinician, "Patient", "p-x")
    assert ei.value.reason == RejectReason.NOT_FOUND


def test_researcher_cannot_write(svc, researcher):
    log_case("researcher-write", inputs=PATIENT, parties=[researcher.subject],
             rationale="researcher/research 无写权限，须以 forbidden 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(researcher, PATIENT, source="his")
    assert ei.value.reason == RejectReason.FORBIDDEN


def test_export_masked_for_researcher(svc, clinician, researcher):
    _seed(svc, clinician)
    log_case("export-masked", inputs={"export": "Patient/p-1"},
             parties=[researcher.subject],
             rationale="研究用途导出须脱敏 name/phone，gender 保留")
    out = svc.export(researcher, "Patient", "p-1")
    assert out["payload"]["name"] == MASK
    assert out["payload"]["phone"] == MASK
    assert out["payload"]["gender"] == "male"
    assert set(out["masked_fields"]) == {"name", "phone"}


def test_export_unmasked_for_clinician(svc, clinician):
    _seed(svc, clinician)
    log_case("export-unmasked", inputs={"export": "Patient/p-1"},
             parties=[clinician.subject],
             rationale="诊疗用途导出按诊疗需要可见明文")
    out = svc.export(clinician, "Patient", "p-1")
    assert out["payload"]["name"] == {"text": "张三"}  # 字符串姓名归一为规范结构
    assert out["masked_fields"] == []


def test_audit_complete_and_no_sensitive_leak(svc, clinician, researcher):
    _seed(svc, clinician)
    svc.export(researcher, "Patient", "p-1")
    with pytest.raises(InteropError):
        svc.history(researcher, "Patient", "p-1")
    log_case("audit", inputs={"ops": ["ingest", "export", "denied-history"]},
             parties=[clinician.subject, researcher.subject],
             rationale="审计须含主体/动作/结果/脱敏字段，且不含原始敏感值")
    records = svc.audit.all()
    assert len(records) == 3
    write_rec, export_rec, denied_rec = records
    assert write_rec.action == "write" and write_rec.outcome == "success"
    assert write_rec.subject == clinician.subject
    assert export_rec.action == "export" and export_rec.outcome == "success"
    assert set(export_rec.masked_fields) == {"name", "phone"}
    assert denied_rec.outcome == f"denied:{RejectReason.FORBIDDEN.value}"
    # 审计记录中不得出现原始敏感值
    blob = repr(records)
    assert "张三" not in blob and "13800001111" not in blob
