"""审计日志：记录操作主体、动作、结果与被脱敏字段，不记录原始敏感值。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AuditRecord:
    subject: str
    role: str
    purpose: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str            # "success" 或 "denied:<reason>"
    masked_fields: list[str] = field(default_factory=list)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLog:
    """线程安全的内存审计日志。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[AuditRecord] = []

    def record(self, rec: AuditRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def all(self) -> list[AuditRecord]:
        with self._lock:
            return list(self._records)

    def query(
        self,
        *,
        subject: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditRecord]:
        """按操作主体 / 动作 / 结果 / 时间范围筛选审计记录。

        outcome 支持精确匹配（如 "success"、"denied:not_found"），
        也支持 "denied" 匹配所有 denied:* 记录。
        返回的记录只含字段名等元数据，绝不含原始敏感值。
        """
        with self._lock:
            records = list(self._records)

        def match(rec: AuditRecord) -> bool:
            if subject is not None and rec.subject != subject:
                return False
            if action is not None and rec.action != action:
                return False
            if outcome is not None:
                if outcome == "denied":
                    if not rec.outcome.startswith("denied:"):
                        return False
                elif rec.outcome != outcome:
                    return False
            if since is not None and rec.at < since:
                return False
            return not (until is not None and rec.at > until)

        return [rec for rec in records if match(rec)]

    @staticmethod
    def summarize(records: list[AuditRecord]) -> dict:
        """对筛选结果做汇总，便于对账：按动作与结果计数。"""
        by_action: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        masked_total = 0
        for rec in records:
            by_action[rec.action] = by_action.get(rec.action, 0) + 1
            by_outcome[rec.outcome] = by_outcome.get(rec.outcome, 0) + 1
            masked_total += len(rec.masked_fields)
        return {"total": len(records), "by_action": by_action,
                "by_outcome": by_outcome, "masked_field_events": masked_total}
