"""业务编排：接入归一、版本化写入、查询、导出。"""
from __future__ import annotations

from .audit import AuditLog, AuditRecord
from .auth import Action, authorize
from .coding import normalize_code, normalize_time
from .errors import InteropError, RejectReason
from .masking import mask_for_export
from .models import Actor, Resource, ResourceType
from .normalize import normalize_patient_payload
from .store import VersionedStore

# 不同来源可能使用的字段名变体（camelCase / snake_case）
_TYPE_KEYS = ("resource_type", "resourceType")
_ID_KEYS = ("id", "resource_id")
_TIME_KEYS = ("effective_time", "effectiveDateTime")


def _pick(raw: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in raw:
            return raw[k]
    return None


class InteropService:
    def __init__(self, store: VersionedStore | None = None, audit: AuditLog | None = None) -> None:
        self.store = store or VersionedStore()
        self.audit = audit or AuditLog()

    # ---- 内部工具 ----

    def _audit(self, actor: Actor, action: Action, rtype: str, rid: str,
               outcome: str, masked: list[str] | None = None) -> None:
        self.audit.record(AuditRecord(
            subject=actor.subject, role=actor.role.value, purpose=actor.purpose.value,
            action=action.value, resource_type=rtype, resource_id=rid,
            outcome=outcome, masked_fields=masked or [],
        ))

    def _normalize(self, raw: dict, source: str) -> Resource:
        """校验并归一化一条来源记录；任何不合格都抛出可区分原因的 InteropError。"""
        if not isinstance(raw, dict):
            raise InteropError(RejectReason.UNSUPPORTED_RESOURCE, "记录必须是对象")

        type_raw = _pick(raw, _TYPE_KEYS)
        try:
            rtype = ResourceType(str(type_raw))
        except ValueError:
            raise InteropError(RejectReason.UNSUPPORTED_RESOURCE,
                               f"不支持的资源类型: {type_raw}",
                               details={"resource_type": type_raw}) from None

        rid = _pick(raw, _ID_KEYS)
        if not rid or not isinstance(rid, str):
            raise InteropError(RejectReason.MISSING_FIELD, "缺少必填字段: id",
                               details={"field": "id"})

        payload = {k: v for k, v in raw.items()
                   if k not in _TYPE_KEYS + _ID_KEYS + _TIME_KEYS}

        effective_time = None
        if rtype == ResourceType.OBSERVATION:
            code_obj = raw.get("code")
            if not isinstance(code_obj, dict):
                raise InteropError(RejectReason.MISSING_FIELD,
                                   "Observation 缺少必填字段: code",
                                   details={"field": "code"})
            coding = normalize_code(code_obj.get("system"), code_obj.get("code"))
            payload["code"] = {"system": coding.system, "code": coding.code,
                               "display": coding.display}

            time_raw = _pick(raw, _TIME_KEYS)
            if time_raw is None:
                raise InteropError(RejectReason.MISSING_FIELD,
                                   "Observation 缺少必填字段: effective_time",
                                   details={"field": "effective_time"})
            effective_time = normalize_time(time_raw)
            payload["effective_time"] = effective_time.isoformat()

        if rtype == ResourceType.PATIENT:
            # Patient 全字段归一：字段名/取值统一为规范形态，
            # 无法识别的字段或取值以可区分原因拒绝（不透传、不静默写入）
            payload = normalize_patient_payload(payload)

        return Resource(resource_type=rtype, resource_id=rid, source=source,
                        payload=payload, effective_time=effective_time)

    # ---- 对外操作 ----

    def ingest(self, actor: Actor, raw: dict, *, source: str,
               expected_version: int | None = None,
               idempotency_key: str | None = None) -> dict:
        """接入并归一化一条来源记录，写入版本化存储。"""
        rid = str(_pick(raw, _ID_KEYS) if isinstance(raw, dict) else "") or "-"
        rtype = str(_pick(raw, _TYPE_KEYS) if isinstance(raw, dict) else "-")
        try:
            authorize(actor, Action.WRITE, ResourceType.OBSERVATION)  # 写权限与类型无关
            resource = self._normalize(raw, source)
            version, created = self.store.put(
                resource, expected_version=expected_version,
                idempotency_key=idempotency_key, actor_subject=actor.subject)
        except InteropError as e:
            self._audit(actor, Action.WRITE, rtype, rid, f"denied:{e.reason.value}")
            raise
        self._audit(actor, Action.WRITE, resource.resource_type.value,
                    resource.resource_id, "success")
        return {
            "resource_type": resource.resource_type.value,
            "resource_id": resource.resource_id,
            "version": version.version,
            "created": created,
            "normalized": resource.payload,
        }

    def _get_or_404(self, rtype: ResourceType, rid: str):
        current = self.store.get_current(rtype, rid)
        if current is None:
            raise InteropError(RejectReason.NOT_FOUND,
                               f"资源不存在: {rtype.value}/{rid}",
                               details={"resource_type": rtype.value, "resource_id": rid})
        return current

    def _parse_type(self, resource_type: str) -> ResourceType:
        try:
            return ResourceType(resource_type)
        except ValueError:
            raise InteropError(RejectReason.UNSUPPORTED_RESOURCE,
                               f"不支持的资源类型: {resource_type}") from None

    def read(self, actor: Actor, resource_type: str, resource_id: str) -> dict:
        rtype = self._parse_type(resource_type)
        try:
            authorize(actor, Action.READ, rtype)
            current = self._get_or_404(rtype, resource_id)
        except InteropError as e:
            self._audit(actor, Action.READ, resource_type, resource_id,
                        f"denied:{e.reason.value}")
            raise
        self._audit(actor, Action.READ, resource_type, resource_id, "success")
        return {"resource_type": rtype.value, "resource_id": resource_id,
                "version": current.version, "source": current.resource.source,
                "payload": current.resource.payload}

    def history(self, actor: Actor, resource_type: str, resource_id: str) -> list[dict]:
        rtype = self._parse_type(resource_type)
        try:
            authorize(actor, Action.HISTORY, rtype)
            chain = self.store.history(rtype, resource_id)
            if not chain:
                raise InteropError(RejectReason.NOT_FOUND,
                                   f"资源不存在: {rtype.value}/{resource_id}")
        except InteropError as e:
            self._audit(actor, Action.HISTORY, resource_type, resource_id,
                        f"denied:{e.reason.value}")
            raise
        self._audit(actor, Action.HISTORY, resource_type, resource_id, "success")
        return [{"version": v.version, "updated_at": v.updated_at.isoformat(),
                 "updated_by": v.updated_by, "payload": v.resource.payload}
                for v in chain]

    def export(self, actor: Actor, resource_type: str, resource_id: str) -> dict:
        """按策略脱敏后导出，并记录审计（含被脱敏字段，不含原始敏感值）。"""
        rtype = self._parse_type(resource_type)
        try:
            authorize(actor, Action.EXPORT, rtype)
            current = self._get_or_404(rtype, resource_id)
        except InteropError as e:
            self._audit(actor, Action.EXPORT, resource_type, resource_id,
                        f"denied:{e.reason.value}")
            raise
        masked_payload, masked_fields = mask_for_export(current.resource, actor)
        self._audit(actor, Action.EXPORT, resource_type, resource_id,
                    "success", masked_fields)
        return {"resource_type": rtype.value, "resource_id": resource_id,
                "version": current.version, "payload": masked_payload,
                "masked_fields": masked_fields}

    def export_batch(self, actor: Actor, resource_type: str,
                     resource_ids: list[str]) -> dict:
        """批量导出：一次调用导出一批资源，逐项给出成功/拒绝及原因。

        一致性：所有资源在同一快照点读取（见 VersionedStore.snapshot），
        导出结果对应同一个完整版本切面，不会混杂新旧值或半截状态。
        脱敏与审计与单条导出完全对齐：逐项审计，masked_fields 一一对应。
        """
        rtype = self._parse_type(resource_type)
        try:
            authorize(actor, Action.EXPORT, rtype)
        except InteropError as e:
            self._audit(actor, Action.EXPORT, resource_type, ",".join(resource_ids) or "-",
                        f"denied:{e.reason.value}")
            raise
        if not resource_ids:
            raise InteropError(RejectReason.MISSING_FIELD,
                               "批量导出缺少资源 id 列表",
                               details={"field": "resource_ids"})

        snap = self.store.snapshot([(rtype, rid) for rid in resource_ids])
        items: list[dict] = []
        for rid in resource_ids:
            current = snap[(rtype, rid)]
            if current is None:
                self._audit(actor, Action.EXPORT, rtype.value, rid,
                            f"denied:{RejectReason.NOT_FOUND.value}")
                items.append({"resource_id": rid, "status": "rejected",
                              "reason": RejectReason.NOT_FOUND.value})
                continue
            masked_payload, masked_fields = mask_for_export(current.resource, actor)
            self._audit(actor, Action.EXPORT, rtype.value, rid, "success", masked_fields)
            items.append({"resource_id": rid, "status": "ok",
                          "version": current.version, "payload": masked_payload,
                          "masked_fields": masked_fields})
        ok = sum(1 for it in items if it["status"] == "ok")
        return {"resource_type": rtype.value, "consistent_snapshot": True,
                "items": items,
                "summary": {"total": len(items), "ok": ok,
                            "rejected": len(items) - ok}}

    # ---- 审计检索（仅运维角色） ----

    def _query_audit(self, actor: Actor, *, subject=None, action=None,
                     outcome=None, since=None, until=None):
        """授权 + 解析时间范围 + 查询，返回匹配的审计记录。"""
        try:
            authorize(actor, Action.AUDIT_QUERY)
            since_dt = normalize_time(since) if since is not None else None
            until_dt = normalize_time(until) if until is not None else None
        except InteropError as e:
            self._audit(actor, Action.AUDIT_QUERY, "-", "-",
                        f"denied:{e.reason.value}")
            raise
        records = self.audit.query(subject=subject, action=action, outcome=outcome,
                                   since=since_dt, until=until_dt)
        self._audit(actor, Action.AUDIT_QUERY, "-", "-", "success")
        return records

    @staticmethod
    def _serialize_audit(records) -> list[dict]:
        # 审计记录只含主体/动作/结果/被脱敏字段名，绝不含原始敏感值
        return [{"subject": r.subject, "role": r.role, "purpose": r.purpose,
                 "action": r.action, "resource_type": r.resource_type,
                 "resource_id": r.resource_id, "outcome": r.outcome,
                 "masked_fields": list(r.masked_fields), "at": r.at.isoformat()}
                for r in records]

    def search_audit(self, actor: Actor, *, subject=None, action=None,
                     outcome=None, since=None, until=None) -> list[dict]:
        """按主体/动作/结果/时间范围检索审计（仅运维角色）。"""
        return self._serialize_audit(self._query_audit(
            actor, subject=subject, action=action, outcome=outcome,
            since=since, until=until))

    def audit_summary(self, actor: Actor, *, subject=None, action=None,
                      outcome=None, since=None, until=None) -> dict:
        """对筛选结果汇总计数，便于对账（仅运维角色）。"""
        records = self._query_audit(actor, subject=subject, action=action,
                                    outcome=outcome, since=since, until=until)
        return AuditLog.summarize(records)
