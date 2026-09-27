"""按角色与用途的访问控制。"""
from __future__ import annotations

from enum import Enum

from .errors import InteropError, RejectReason
from .models import Actor, Purpose, ResourceType, Role


class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    EXPORT = "export"
    HISTORY = "history"


# 访问控制策略：(角色, 用途) -> 允许的动作集合
# - admin: 全部动作（运维用途）
# - clinician + treatment: 读写、导出、查看历史
# - researcher + research: 仅读取与导出（导出由脱敏策略保护），不可写入
_POLICY: dict[tuple[Role, Purpose], set[Action]] = {
    (Role.ADMIN, Purpose.OPERATIONS): {Action.READ, Action.WRITE, Action.EXPORT, Action.HISTORY},
    (Role.CLINICIAN, Purpose.TREATMENT): {Action.READ, Action.WRITE, Action.EXPORT, Action.HISTORY},
    (Role.RESEARCHER, Purpose.RESEARCH): {Action.READ, Action.EXPORT},
}


def allowed_actions(actor: Actor) -> set[Action]:
    return _POLICY.get((actor.role, actor.purpose), set())


def authorize(actor: Actor, action: Action, resource_type: ResourceType) -> None:
    """越权时抛出 InteropError(FORBIDDEN)（与 NOT_FOUND 可区分）。"""
    if action not in allowed_actions(actor):
        raise InteropError(
            RejectReason.FORBIDDEN,
            f"主体 {actor.subject}（{actor.role.value}/{actor.purpose.value}）"
            f"无权对 {resource_type.value} 执行 {action.value}",
            details={"subject": actor.subject, "role": actor.role.value,
                     "purpose": actor.purpose.value, "action": action.value},
        )
