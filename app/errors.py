"""可区分的错误类型与拒绝原因码。"""
from __future__ import annotations

from enum import Enum


class RejectReason(str, Enum):
    """写入/访问被拒绝的可区分原因。"""

    MISSING_FIELD = "missing_field"            # 必填字段缺失
    INVALID_CODE = "invalid_code"              # 编码非法或不在约定体系内
    INVALID_TIME = "invalid_time"              # 时间语义非法
    INVALID_VALUE = "invalid_value"            # 字段取值格式无法识别
    UNSUPPORTED_FIELD = "unsupported_field"    # 来源字段名无法识别（不透传、不静默写入）
    UNSUPPORTED_RESOURCE = "unsupported_resource"  # 来源格式/资源类型不支持
    VERSION_CONFLICT = "version_conflict"      # 并发版本冲突
    NOT_FOUND = "not_found"                    # 资源不存在
    FORBIDDEN = "forbidden"                    # 越权访问
    DUPLICATE_IN_FLIGHT = "duplicate_in_flight"  # 同一幂等键正在处理


class InteropError(Exception):
    """服务层统一异常，携带可区分的原因码。"""

    def __init__(self, reason: RejectReason, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details or {}
