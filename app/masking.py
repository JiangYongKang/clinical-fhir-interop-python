"""导出脱敏策略：按角色/用途对敏感字段脱敏。"""
from __future__ import annotations

from .models import Actor, Purpose, Resource, ResourceType, Role

MASK = "***MASKED***"

# 各资源类型的敏感字段（payload 内的顶层字段路径）
_SENSITIVE_FIELDS: dict[ResourceType, set[str]] = {
    ResourceType.PATIENT: {"name", "identifier", "phone", "address", "birth_date"},
    ResourceType.OBSERVATION: {"subject_name", "note"},
}

# 需要脱敏的 (角色, 用途) 组合：研究用途导出必须脱敏；
# 临床/运维用途按诊疗需要可见明文。
_MASK_REQUIRED: set[tuple[Role, Purpose]] = {
    (Role.RESEARCHER, Purpose.RESEARCH),
}


def mask_for_export(resource: Resource, actor: Actor) -> tuple[dict, list[str]]:
    """返回 (脱敏后的 payload, 被脱敏的字段路径列表)。

    不修改原 payload；审计只记录被脱敏的字段名，绝不记录原始值。
    """
    payload = dict(resource.payload)
    if (actor.role, actor.purpose) not in _MASK_REQUIRED:
        return payload, []

    masked: list[str] = []
    for field_path in sorted(_SENSITIVE_FIELDS.get(resource.resource_type, set())):
        if field_path in payload:
            payload[field_path] = MASK
            masked.append(field_path)
    return payload, masked
