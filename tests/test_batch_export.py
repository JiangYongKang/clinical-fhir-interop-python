"""批量导出：一批患者一次拿结果，部分成功/部分失败各自有原因；
并发写入下导出对应同一一致快照；脱敏清单与审计一一对应。"""
import threading

import pytest

from app.errors import InteropError, RejectReason
from app.masking import MASK
from tests.conftest import log_case


def _patient(pid: str, name: str, phone: str) -> dict:
    return {"resource_type": "Patient", "id": pid, "name": name,
            "birthDate": "1985-03-04", "phone": phone, "gender": "female"}


def _seed_batch(svc, clinician, n: int = 5) -> list[str]:
    ids = [f"bp-{i}" for i in range(n)]
    for i, pid in enumerate(ids):
        svc.ingest(clinician, _patient(pid, f"Wang {i}", f"1390000000{i}"),
                   source="his")
    return ids


def test_batch_export_partial_success(svc, clinician, researcher):
    ids = _seed_batch(svc, clinician, 3)
    requested = ids + ["bp-x", "bp-y"]  # 两个不存在
    log_case("batch-partial", inputs={"resource_ids": requested},
             parties=[researcher.subject],
             rationale="存在的导出成功且脱敏，不存在的逐项 denied:not_found，汇总计数正确")
    out = svc.batch_export(researcher, "Patient", requested)
    assert out["summary"] == {"total": 5, "exported": 3, "denied": 2}
    by_id = {r["resource_id"]: r for r in out["results"]}
    for pid in ids:
        item = by_id[pid]
        assert item["status"] == "exported" and item["version"] == 1
        assert item["payload"]["name"] == MASK
        assert item["payload"]["birth_date"] == MASK
        assert item["payload"]["phone"] == MASK
        assert item["payload"]["gender"] == "female"
        assert set(item["masked_fields"]) == {"name", "birth_date", "phone"}
    for pid in ("bp-x", "bp-y"):
        assert by_id[pid] == {"resource_id": pid, "status": "denied",
                              "reason": "not_found"}
    # 审计与结果一一对应：5 条导出审计，脱敏清单逐项吻合
    export_recs = [r for r in svc.audit.all() if r.action == "export"]
    assert len(export_recs) == 5
    for rec in export_recs:
        item = by_id[rec.resource_id]
        if rec.outcome == "success":
            assert rec.masked_fields == item["masked_fields"]
        else:
            assert rec.outcome == "denied:not_found" and item["status"] == "denied"


def test_batch_export_forbidden_for_empty_ids(svc, clinician, researcher):
    log_case("batch-empty-ids", inputs={"resource_ids": []},
             parties=[researcher.subject],
             rationale="空列表须以 missing_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.batch_export(researcher, "Patient", [])
    assert ei.value.reason == RejectReason.MISSING_FIELD


def test_batch_export_consistent_under_concurrent_writes(svc, clinician, researcher):
    """导出期间并发写入：结果必须全部来自同一快照，不混搭新旧版本。"""
    ids = _seed_batch(svc, clinician, 6)
    log_case("batch-snapshot-consistency",
             inputs={"ids": ids, "writers": "每资源一个并发写线程"},
             parties=[researcher.subject, clinician.subject],
             rationale="批量导出取同一快照：每个资源的版本要么全是 v1（快照在写入前），"
                       "要么全是 v2（快照在写入后），绝不允许 v1/v2 混搭或半截状态")
    barrier = threading.Barrier(len(ids) + 1)

    def writer(pid: str):
        barrier.wait()  # 与导出同时起跑
        svc.ingest(clinician, dict(_patient(pid, "New Name", "13000000000")),
                   source="his", expected_version=1)

    writers = [threading.Thread(target=writer, args=(pid,)) for pid in ids]
    for t in writers:
        t.start()
    barrier.wait()
    out = svc.batch_export(researcher, "Patient", ids)
    for t in writers:
        t.join()

    versions = {r["version"] for r in out["results"]}
    assert out["summary"]["exported"] == len(ids)
    assert len(versions) == 1, f"快照不一致，出现版本混搭: {versions}"
    assert versions in ({1}, {2})
    # 所有写入最终成功，当前版本为 v2
    for pid in ids:
        assert svc.read(clinician, "Patient", pid)["version"] == 2


def test_batch_export_researcher_masked_audit_aligned(svc, clinician, researcher):
    ids = _seed_batch(svc, clinician, 4)
    log_case("batch-mask-audit", inputs={"ids": ids},
             parties=[researcher.subject],
             rationale="研究用途批量导出逐项脱敏，审计 masked_fields 与导出结果一一对应，"
                       "且不泄露原始敏感值")
    out = svc.batch_export(researcher, "Patient", ids)
    blob = repr(out)
    for i in range(4):
        assert f"Wang {i}" not in blob and f"1390000000{i}" not in blob
    recs = [r for r in svc.audit.all()
            if r.action == "export" and r.outcome == "success"]
    assert len(recs) == 4
    for rec, item in zip(
            sorted(recs, key=lambda r: r.resource_id),
            sorted(out["results"], key=lambda r: r["resource_id"])):
        assert rec.resource_id == item["resource_id"]
        assert rec.masked_fields == item["masked_fields"]
