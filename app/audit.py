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
