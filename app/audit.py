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
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[AuditRecord]:
        """按操作主体 / 动作 / 结果 / 时间范围过滤（条件之间为与关系）。

        outcome 支持精确匹配（如 "success"）或前缀匹配（如 "denied" 匹配
        所有 "denied:<reason>"）。审计记录本身只含字段名、绝不含原始敏感值，
        因此检索结果天然不泄露敏感内容。
        """
        with self._lock:
            records = list(self._records)

        def match(rec: AuditRecord) -> bool:
            if subject is not None and rec.subject != subject:
                return False
            if action is not None and rec.action != action:
                return False
            if outcome is not None and not (
                rec.outcome == outcome or rec.outcome.startswith(outcome + ":")
            ):
                return False
            if start is not None and rec.at < start:
                return False
            return not (end is not None and rec.at > end)

        return [rec for rec in records if match(rec)]

    @staticmethod
    def summarize(records: list[AuditRecord]) -> dict:
        """对账汇总：总数 + 按动作、按结果分类计数。"""
        by_action: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        for rec in records:
            by_action[rec.action] = by_action.get(rec.action, 0) + 1
            by_outcome[rec.outcome] = by_outcome.get(rec.outcome, 0) + 1
        return {"total": len(records), "by_action": by_action,
                "by_outcome": by_outcome}
