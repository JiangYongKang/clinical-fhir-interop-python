"""本地版本化存储：乐观并发、历史版本可追溯、幂等写入。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from .errors import InteropError, RejectReason
from .models import Resource, ResourceType, ResourceVersion


class VersionedStore:
    """内存版本地存储，按 (resource_type, resource_id) 维护版本链。

    并发语义：乐观并发控制 —— 写入方必须携带期望版本号 expected_version，
    与当前版本不一致即拒绝（VERSION_CONFLICT），避免覆盖式丢失更新。
    幂等语义：同一 idempotency_key 重复提交返回首次写入结果，不产生新版本。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._versions: dict[tuple[ResourceType, str], list[ResourceVersion]] = {}
        # 幂等键 -> (资源类型, 资源ID, 写入得到的版本号)
        self._idempotency: dict[str, tuple[ResourceType, str, int]] = {}

    def get_current(self, resource_type: ResourceType, resource_id: str) -> ResourceVersion | None:
        with self._lock:
            chain = self._versions.get((resource_type, resource_id))
            return chain[-1] if chain else None

    def history(self, resource_type: ResourceType, resource_id: str) -> list[ResourceVersion]:
        with self._lock:
            return list(self._versions.get((resource_type, resource_id), []))

    def put(
        self,
        resource: Resource,
        *,
        expected_version: int | None,
        idempotency_key: str | None,
        actor_subject: str,
    ) -> tuple[ResourceVersion, bool]:
        """写入新版本。

        - expected_version 与当前版本不一致 -> InteropError(VERSION_CONFLICT)
          （新建资源须传 expected_version=0 或 None）
        - idempotency_key 重复提交 -> 返回首次结果，created=False
        返回 (版本, 是否新建版本)。
        """
        key = (resource.resource_type, resource.resource_id)
        with self._lock:
            if idempotency_key is not None and idempotency_key in self._idempotency:
                rtype, rid, ver = self._idempotency[idempotency_key]
                chain = self._versions[(rtype, rid)]
                return chain[ver - 1], False

            chain = self._versions.setdefault(key, [])
            current_version = chain[-1].version if chain else 0
            expected = 0 if expected_version is None else expected_version
            if expected != current_version:
                raise InteropError(
                    RejectReason.VERSION_CONFLICT,
                    f"版本冲突：期望 v{expected}，当前 v{current_version}",
                    details={"expected": expected, "current": current_version,
                             "resource_id": resource.resource_id},
                )

            version = ResourceVersion(
                resource=resource,
                version=current_version + 1,
                updated_at=datetime.now(timezone.utc),
                updated_by=actor_subject,
            )
            chain.append(version)
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = (
                    resource.resource_type, resource.resource_id, version.version)
            return version, True
