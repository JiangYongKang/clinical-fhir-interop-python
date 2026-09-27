"""编码归一与时间语义归一：将不同来源的编码/时间映射到约定体系。"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from .errors import InteropError, RejectReason
from .models import Coding

# 约定的目标编码体系
LOINC = "http://loinc.org"
LOCAL = "urn:clinical:local"

# 来源系统别名 -> 规范系统
_SYSTEM_ALIASES = {
    "loinc": LOINC,
    "http://loinc.org": LOINC,
    "urn:oid:2.16.840.1.113883.6.1": LOINC,
    "local": LOCAL,
    "internal": LOCAL,
    "urn:clinical:local": LOCAL,
}

# 常见来源本地码 -> LOINC 映射（演示不同来源格式不一致时的统一映射）
_LOCAL_TO_LOINC = {
    "HR": ("8867-4", "Heart rate"),
    "BP-SYS": ("8480-6", "Systolic blood pressure"),
    "BP-DIA": ("8462-4", "Diastolic blood pressure"),
    "TEMP": ("8310-5", "Body temperature"),
    "SPO2": ("2708-6", "Oxygen saturation"),
    "GLU": ("2339-0", "Glucose"),
}

_LOINC_CODE_RE = re.compile(r"^\d{1,5}-\d$")
_LOCAL_CODE_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,31}$")


def normalize_code(system: str | None, code: str | None) -> Coding:
    """将来源编码映射到约定编码体系（LOINC/内部体系）。

    非法或未知编码抛出 InteropError(INVALID_CODE)。
    """
    if not system or not isinstance(system, str):
        raise InteropError(RejectReason.INVALID_CODE, "编码体系缺失或非法",
                           details={"system": system, "code": code})
    if not code or not isinstance(code, str):
        raise InteropError(RejectReason.INVALID_CODE, "编码值缺失或非法",
                           details={"system": system, "code": code})

    canonical_system = _SYSTEM_ALIASES.get(system.strip().lower())
    if canonical_system is None:
        raise InteropError(RejectReason.INVALID_CODE, f"未识别的编码体系: {system}",
                           details={"system": system, "code": code})

    code = code.strip()
    if canonical_system == LOINC:
        if not _LOINC_CODE_RE.match(code):
            raise InteropError(RejectReason.INVALID_CODE, f"非法 LOINC 编码: {code}",
                               details={"system": system, "code": code})
        return Coding(system=LOINC, code=code)

    # 本地体系：优先映射到 LOINC，否则保留为本地码
    if not _LOCAL_CODE_RE.match(code):
        raise InteropError(RejectReason.INVALID_CODE, f"非法本地编码: {code}",
                           details={"system": system, "code": code})
    mapped = _LOCAL_TO_LOINC.get(code)
    if mapped:
        return Coding(system=LOINC, code=mapped[0], display=mapped[1])
    return Coding(system=LOCAL, code=code)


def normalize_time(value: object) -> datetime:
    """将来源时间（ISO8601 字符串 / epoch 秒 / date）统一为 UTC 时间。

    无法解析时抛出 InteropError(INVALID_TIME)。
    """
    dt: datetime
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise InteropError(RejectReason.INVALID_TIME, f"非法 epoch 时间: {value}") from exc
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise InteropError(RejectReason.INVALID_TIME, f"无法解析的时间: {value}") from exc
    else:
        raise InteropError(RejectReason.INVALID_TIME, f"不支持的时间类型: {type(value).__name__}")

    if dt.tzinfo is None:
        # 无时区信息的来源时间按 UTC 解释，保证时间语义一致
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
