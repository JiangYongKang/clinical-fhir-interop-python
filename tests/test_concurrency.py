"""版本与并发：重复提交结果稳定、并发冲突可区分拒绝、历史版本可追溯。"""
import threading

import pytest

from app.errors import InteropError, RejectReason
from tests.conftest import log_case

OBS = {
    "resourceType": "Observation",
    "id": "obs-c",
    "code": {"system": "local", "code": "HR"},
    "effectiveDateTime": "2026-09-27T10:00:00Z",
    "value": 70,
}


def test_duplicate_submission_stable(svc, clinician):
    log_case("idempotent-replay", inputs={**OBS, "idempotency_key": "key-1"},
             parties=[clinician.subject],
             rationale="同一幂等键重复提交须返回首次结果且不产生新版本")
    r1 = svc.ingest(clinician, OBS, source="devA", idempotency_key="key-1")
    r2 = svc.ingest(clinician, dict(OBS, value=99), source="devA", idempotency_key="key-1")
    assert (r1["version"], r1["created"]) == (1, True)
    assert (r2["version"], r2["created"]) == (1, False)
    assert svc.read(clinician, "Observation", "obs-c")["payload"]["value"] == 70


def test_stale_version_conflict(svc, clinician):
    log_case("stale-version", inputs={"expected_version": 0},
             parties=[clinician.subject, "dr-second"],
             rationale="基于过期版本的写入须以 version_conflict 拒绝，避免覆盖式丢失更新")
    svc.ingest(clinician, OBS, source="devA")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, dict(OBS, value=80), source="devA", expected_version=0)
    assert ei.value.reason == RejectReason.VERSION_CONFLICT
    # 携带正确版本号则可写入
    out = svc.ingest(clinician, dict(OBS, value=80), source="devA", expected_version=1)
    assert out["version"] == 2


def test_concurrent_writes_no_lost_update(svc, clinician):
    log_case("concurrent-writes", inputs={"writers": 8, "all_expected_version": 1},
             parties=[clinician.subject] + [f"thread-{i}" for i in range(8)],
             rationale="8 个并发写同一版本号：恰好 1 个成功，其余全部 version_conflict")
    svc.ingest(clinician, OBS, source="devA")
    results: list[str] = []
    lock = threading.Lock()

    def writer(i: int):
        try:
            svc.ingest(clinician, dict(OBS, value=70 + i), source="devA",
                       expected_version=1)
            outcome = "success"
        except InteropError as e:
            outcome = e.reason.value
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("success") == 1
    assert results.count(RejectReason.VERSION_CONFLICT.value) == 7
    current = svc.read(clinician, "Observation", "obs-c")
    assert current["version"] == 2  # 只前进一个版本，无丢失更新


def test_history_traceable(svc, clinician):
    log_case("history", inputs={"writes": 3}, parties=[clinician.subject],
             rationale="每次写入产生一个版本，历史须完整可追溯")
    svc.ingest(clinician, OBS, source="devA")
    svc.ingest(clinician, dict(OBS, value=71), source="devA", expected_version=1)
    svc.ingest(clinician, dict(OBS, value=72), source="devA", expected_version=2)
    hist = svc.history(clinician, "Observation", "obs-c")
    assert [h["version"] for h in hist] == [1, 2, 3]
    assert [h["payload"]["value"] for h in hist] == [70, 71, 72]
    assert all(h["updated_by"] == clinician.subject for h in hist)
