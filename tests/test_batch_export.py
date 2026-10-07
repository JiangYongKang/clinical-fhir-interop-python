"""批量导出：一次调用导出一批患者，逐项成功/拒绝及原因；
并发写入下结果必须对应同一完整版本切面；脱敏清单与审计一一对应。"""
import threading
import time

from app.masking import MASK
from tests.conftest import log_case


def _patient(rid, name):
    return {"resourceType": "Patient", "id": rid, "name": name,
            "birthDate": "1980-05-01", "phone": "13800001111"}


def _seed(svc, actor, n=3):
    for i in range(n):
        svc.ingest(actor, _patient(f"p-{i}", f"Patient-{i}"), source="src")


def test_batch_partial_success_and_failure(svc, clinician, researcher):
    _seed(svc, clinician, 3)
    log_case("batch-partial", inputs={"ids": ["p-0", "p-1", "p-x"]},
             parties=[researcher.subject],
             rationale="存在的成功并脱敏，不存在的逐项给出 not_found，互不影响")
    out = svc.export_batch(researcher, "Patient", ["p-0", "p-1", "p-x"])
    assert out["summary"] == {"total": 3, "ok": 2, "rejected": 1}
    by_id = {it["resource_id"]: it for it in out["items"]}
    assert by_id["p-0"]["status"] == "ok" and by_id["p-0"]["version"] == 1
    assert by_id["p-0"]["payload"]["name"] == MASK
    assert set(by_id["p-0"]["masked_fields"]) == {"name", "birth_date", "phone"}
    assert by_id["p-x"] == {"resource_id": "p-x", "status": "rejected",
                            "reason": "not_found"}
    # 原始敏感值不出现在批量结果中
    assert "Patient-0" not in str(out) and "13800001111" not in str(out)


def test_batch_masking_matches_audit(svc, clinician, researcher):
    _seed(svc, clinician, 2)
    log_case("batch-audit-align", inputs={"ids": ["p-0", "p-1", "p-x"]},
             parties=[researcher.subject],
             rationale="每项的 masked_fields 与审计记录一一对应，拒绝项也有审计")
    out = svc.export_batch(researcher, "Patient", ["p-0", "p-1", "p-x"])
    audits = [r for r in svc.audit.all()
              if r.action == "export" and r.subject == researcher.subject]
    by_id = {r.resource_id: r for r in audits}
    assert set(by_id) == {"p-0", "p-1", "p-x"}
    for item in out["items"]:
        rec = by_id[item["resource_id"]]
        if item["status"] == "ok":
            assert rec.outcome == "success"
            assert rec.masked_fields == item["masked_fields"]
        else:
            assert rec.outcome == "denied:not_found"


def test_batch_consistent_snapshot_under_concurrent_writes(svc, clinician, researcher):
    """写入线程按 p-0..p-9 顺序逐轮升版；批量导出结果的版本向量必须
    满足‘下标小的版本 >= 下标大的版本’，即对应某个完整时间切面，
    不允许出现前旧后新的混杂状态。"""
    n = 10
    _seed(svc, clinician, n)
    rounds = 30

    def writer():
        for _ in range(rounds):
            for i in range(n):
                ver = svc.read(clinician, "Patient", f"p-{i}")["version"]
                svc.ingest(clinician, _patient(f"p-{i}", f"Patient-{i}"),
                           source="src", expected_version=ver)
            time.sleep(0.0005)

    log_case("batch-consistency",
             inputs={"patients": n, "writer_rounds": rounds},
             parties=[clinician.subject, researcher.subject],
             rationale="任一导出结果须存在时间点 t 使各资源版本恰为 t 时的当前版本："
                       "写入按序进行，故版本向量必须前缀非递增")
    t = threading.Thread(target=writer)
    t.start()
    checked = 0
    while t.is_alive():
        out = svc.export_batch(researcher, "Patient", [f"p-{i}" for i in range(n)])
        versions = [it["version"] for it in out["items"]]
        assert all(versions[i] >= versions[i + 1] for i in range(n - 1)), \
            f"版本向量不对应任何完整切面: {versions}"
        checked += 1
    t.join()
    assert checked > 0


def test_batch_forbidden_for_write_only_check(svc, clinician):
    """无导出权限的主体批量导出被整体拒绝（可区分 forbidden）。"""
    import pytest

    from app.errors import InteropError, RejectReason
    from app.models import Actor, Purpose, Role
    outsider = Actor("x", Role.RESEARCHER, Purpose.TREATMENT)  # 策略外组合
    log_case("batch-forbidden", inputs={"ids": ["p-0"]}, parties=[outsider.subject],
             rationale="策略外角色/用途组合须以 forbidden 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.export_batch(outsider, "Patient", ["p-0"])
    assert ei.value.reason == RejectReason.FORBIDDEN
