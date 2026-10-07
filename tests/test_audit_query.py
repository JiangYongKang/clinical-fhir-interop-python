"""审计检索：按主体/时间范围/动作/结果筛选与汇总，仅运维角色可用，
查询结果不得携带任何已脱敏的原始敏感值。"""
import pytest

from app.errors import InteropError, RejectReason
from tests.conftest import log_case


def _seed(svc, clinician, researcher):
    svc.ingest(clinician, {"resourceType": "Patient", "id": "p-1",
                           "name": "张三", "phone": "13800001111",
                           "birthDate": "1980-05-01"}, source="src")
    svc.export(researcher, "Patient", "p-1")
    svc.ingest(clinician, {"resourceType": "Patient", "id": "p-2",
                           "name": "李四"}, source="src")


def test_query_by_subject_action_outcome(svc, clinician, researcher, ops):
    _seed(svc, clinician, researcher)
    log_case("audit-filter", inputs={"subject": "res-li", "action": "export",
                                     "outcome": "success"},
             parties=[ops.subject],
             rationale="按主体+动作+结果筛选，应只命中研究员的成功导出")
    out = svc.search_audit(ops, subject="res-li", action="export", outcome="success")
    assert len(out) == 1
    rec = out[0]
    assert rec["subject"] == "res-li" and rec["action"] == "export"
    assert rec["outcome"] == "success"
    assert set(rec["masked_fields"]) == {"name", "birth_date", "phone"}
    # outcome=denied 应匹配所有 denied:* 记录
    denied = svc.search_audit(ops, outcome="denied")
    assert all(r["outcome"].startswith("denied:") for r in denied)


def test_query_by_time_range(svc, clinician, researcher, ops):
    _seed(svc, clinician, researcher)
    log_case("audit-time-range", inputs={"until": "2000-01-01",
                                         "since": "2000-01-01"},
             parties=[ops.subject],
             rationale="时间范围之外的记录不可见，范围之内全部可见")
    assert svc.search_audit(ops, until="2000-01-01") == []
    before = len(svc.audit.all())  # 查询本身也会追加一条审计，先取基数
    everything = svc.search_audit(ops, since="2000-01-01")
    assert len(everything) == before


def test_summary_aggregation(svc, clinician, researcher, ops):
    _seed(svc, clinician, researcher)
    log_case("audit-summary", inputs={}, parties=[ops.subject],
             rationale="汇总按动作与结果计数，便于对账")
    summary = svc.audit_summary(ops, subject="dr-wang")
    assert summary["by_action"] == {"write": 2}
    assert summary["by_outcome"] == {"success": 2}
    assert summary["total"] == 2
    masked = svc.audit_summary(ops, subject="res-li")
    assert masked["masked_field_events"] == 3  # name/birth_date/phone


def test_query_forbidden_for_non_ops(svc, clinician, researcher):
    log_case("audit-forbidden", inputs={}, parties=[clinician.subject,
                                                    researcher.subject],
             rationale="非运维角色查询审计须以 forbidden 可区分地拒绝")
    for actor in (clinician, researcher):
        with pytest.raises(InteropError) as ei:
            svc.search_audit(actor)
        assert ei.value.reason == RejectReason.FORBIDDEN
        with pytest.raises(InteropError) as ei2:
            svc.audit_summary(actor)
        assert ei2.value.reason == RejectReason.FORBIDDEN


def test_query_result_contains_no_sensitive_values(svc, clinician, researcher, ops):
    _seed(svc, clinician, researcher)
    log_case("audit-no-leak", inputs={"query": "全部审计"},
             parties=[ops.subject],
             rationale="审计查询结果只含字段名，绝不含张三/手机号等原始敏感值")
    blob = str(svc.search_audit(ops))
    assert "张三" not in blob and "13800001111" not in blob
    assert "1980-05-01" not in blob
    assert "name" in blob  # 只出现被脱敏的字段名
