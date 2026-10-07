"""审计检索：仅运维角色可用，其他角色可区分拒绝；支持按主体/动作/结果/
时间范围过滤与汇总对账；检索结果不泄露任何已脱敏的原始敏感值。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import InteropError, RejectReason
from app.models import Actor, Purpose, Role
from tests.conftest import log_case

PATIENT = {"resource_type": "Patient", "id": "ap-1", "name": "陈秘",
           "birthDate": "1978-11-20", "phone": "13711112222"}


@pytest.fixture
def admin():
    return Actor("ops-zhao", Role.ADMIN, Purpose.OPERATIONS)


def _seed(svc, clinician, researcher):
    svc.ingest(clinician, PATIENT, source="his")
    svc.export(researcher, "Patient", "ap-1")
    with pytest.raises(InteropError):
        svc.history(researcher, "Patient", "ap-1")  # 产生一条 denied 记录


def test_audit_query_forbidden_for_non_ops(svc, clinician, researcher):
    _seed(svc, clinician, researcher)
    log_case("audit-query-forbidden",
             inputs={"query_by": [clinician.subject, researcher.subject]},
             parties=[clinician.subject, researcher.subject],
             rationale="审计检索仅运维角色可用，临床/研究角色须以 forbidden 可区分拒绝")
    for actor in (clinician, researcher):
        with pytest.raises(InteropError) as ei:
            svc.query_audit(actor)
        assert ei.value.reason == RejectReason.FORBIDDEN


def test_audit_query_filters_and_summary(svc, clinician, researcher, admin):
    _seed(svc, clinician, researcher)
    log_case("audit-query-filters",
             inputs={"subject": researcher.subject, "action": "export",
                     "outcome": "denied"},
             parties=[admin.subject],
             rationale="按主体/动作/结果过滤须精确命中；汇总须给出总数与分类计数")
    # 按主体过滤
    out = svc.query_audit(admin, subject=researcher.subject)
    assert out["summary"]["total"] == 2  # export + denied history
    assert all(r["subject"] == researcher.subject for r in out["records"])
    # 按动作过滤
    out = svc.query_audit(admin, action="export")
    assert out["summary"]["total"] == 1
    assert out["summary"]["by_action"] == {"export": 1}
    # 按结果过滤：denied 前缀匹配所有 denied:<reason>
    out = svc.query_audit(admin, outcome="denied")
    assert out["summary"]["total"] == 1
    assert out["records"][0]["outcome"] == "denied:forbidden"
    # 组合过滤 + 汇总结构
    out = svc.query_audit(admin, subject=clinician.subject, outcome="success")
    assert out["summary"]["total"] == 1
    assert out["summary"]["by_outcome"] == {"success": 1}


def test_audit_query_time_range(svc, clinician, researcher, admin):
    _seed(svc, clinician, researcher)
    now = datetime.now(timezone.utc)
    log_case("audit-query-time-range",
             inputs={"start": (now - timedelta(minutes=1)).isoformat(),
                     "end_of_future": (now + timedelta(minutes=1)).isoformat(),
                     "past_window": "昨天"},
             parties=[admin.subject],
             rationale="时间范围过滤：窗口内命中全部记录，过去的窗口命中 0 条")
    out = svc.query_audit(admin, start=(now - timedelta(minutes=1)).isoformat(),
                          end=(now + timedelta(minutes=1)).isoformat())
    assert out["summary"]["total"] == 3
    past = svc.query_audit(admin, end=(now - timedelta(days=1)).isoformat())
    assert past["summary"]["total"] == 0


def test_audit_query_no_sensitive_leak(svc, clinician, researcher, admin):
    _seed(svc, clinician, researcher)
    log_case("audit-query-no-leak", inputs={"query": "全量"},
             parties=[admin.subject],
             rationale="检索结果只含字段名与元数据，不得出现任何已脱敏的原始敏感值")
    out = svc.query_audit(admin)
    blob = repr(out)
    for sensitive in ("陈秘", "1978-11-20", "13711112222"):
        assert sensitive not in blob
    # 导出审计的脱敏清单如实保留
    export_rec = next(r for r in out["records"] if r["action"] == "export")
    assert set(export_rec["masked_fields"]) == {"name", "birth_date", "phone"}


def test_audit_query_itself_audited(svc, clinician, researcher, admin):
    _seed(svc, clinician, researcher)
    log_case("audit-query-self-audit", inputs={"query_count": 1},
             parties=[admin.subject],
             rationale="审计检索本身也须留痕，便于对账")
    svc.query_audit(admin)
    recs = svc.audit.query(action="audit_query", subject=admin.subject)
    assert len(recs) == 1 and recs[0].outcome == "success"
