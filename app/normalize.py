"""Patient 来源字段归一：字段名变体映射 + 取值格式统一。

不同来源对同一语义的写法不一（camelCase / snake_case / 设备缩写），
取值格式也不一（姓名给字符串或结构化对象、出生日期给纯日期或带时区时间、
联系方式给字符串/列表/telecom 对象）。本模块把可识别的写法统一为规范形态；
认不出来的字段名或取值以可区分原因拒绝，绝不原样透传、绝不静默写入。
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .errors import InteropError, RejectReason

# 规范字段名 -> 可接受的来源字段名变体（含规范名自身）
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "patientName", "patient_name", "fullName", "full_name"),
    "birth_date": ("birth_date", "birthDate", "birthdate", "dateOfBirth",
                   "date_of_birth", "dob", "DOB"),
    "phone": ("phone", "phones", "telecom", "phoneNumber", "phone_number",
              "contact", "mobile"),
    "identifier": ("identifier", "identifiers", "mrn", "MRN"),
    "address": ("address", "addr", "homeAddress", "home_address"),
    "gender": ("gender", "sex", "administrativeGender"),
}

# 来源字段名 -> 规范字段名
_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias: canonical for canonical, aliases in _FIELD_ALIASES.items() for alias in aliases
}

_GENDER_ALIASES = {
    "male": "male", "m": "male", "男": "male",
    "female": "female", "f": "female", "女": "female",
    "other": "other", "unknown": "unknown",
}

_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{2,18}[0-9]$")


def _invalid(field: str, value: object, why: str) -> InteropError:
    return InteropError(RejectReason.INVALID_FIELD,
                        f"字段 {field} 取值无法识别: {why}",
                        details={"field": field, "reason": why})


def _norm_name(value: object) -> dict:
    """姓名统一为结构化 {"family": str, "given": [str, ...]}。

    接受：字符串（按空白分词，末词为 family；单词则整体为 family）、
    {"family": str, "given": str | [str]} 对象。
    """
    if isinstance(value, str):
        tokens = value.split()
        if not tokens:
            raise _invalid("name", value, "空字符串")
        if len(tokens) == 1:
            return {"family": tokens[0], "given": []}
        return {"family": tokens[-1], "given": tokens[:-1]}
    if isinstance(value, dict):
        family = value.get("family")
        given = value.get("given", [])
        if not isinstance(family, str) or not family.strip():
            raise _invalid("name", value, "结构化姓名缺少 family")
        if isinstance(given, str):
            given = [given]
        if not isinstance(given, list) or not all(isinstance(g, str) for g in given):
            raise _invalid("name", value, "given 须为字符串或字符串列表")
        return {"family": family.strip(), "given": [g.strip() for g in given]}
    raise _invalid("name", value, f"不支持的姓名类型: {type(value).__name__}")


def _norm_birth_date(value: object) -> str:
    """出生日期统一为 "YYYY-MM-DD"。

    接受：纯日期字符串、date/datetime 对象、带时间的 ISO 字符串
    （含时区；取其书写时的日历日，不做时区换算以免跨日漂移）。
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
            return dt.date().isoformat()
        except ValueError:
            raise _invalid("birth_date", value, "不是合法的日期/时间字符串") from None
    raise _invalid("birth_date", value, f"不支持的出生日期类型: {type(value).__name__}")


def _norm_phone(value: object) -> list[str]:
    """联系方式统一为字符串列表（去空格/横线，保留前导 +）。

    接受：单个字符串、字符串列表、telecom 对象
    （{"system": "phone", "value": "..."} 或其列表）。
    """
    items: list[object]
    if isinstance(value, (str, dict)):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise _invalid("phone", value, f"不支持的联系方式类型: {type(value).__name__}")

    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("value")
        if not isinstance(item, str):
            raise _invalid("phone", value, "联系方式条目须为字符串或含 value 的对象")
        text = item.strip()
        if not _PHONE_RE.match(text):
            raise _invalid("phone", value, f"非法的电话号码: {item}")
        out.append(text.replace(" ", "").replace("-", ""))
    if not out:
        raise _invalid("phone", value, "联系方式为空")
    return out


def _norm_identifier(value: object) -> list[str]:
    """标识符统一为字符串列表。接受：字符串、字符串列表、含 value 的对象列表。"""
    items = [value] if isinstance(value, (str, dict)) else value
    if not isinstance(items, list) or not items:
        raise _invalid("identifier", value, "标识符须为非空字符串/列表")
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("value")
        if not isinstance(item, str) or not item.strip():
            raise _invalid("identifier", value, "标识符条目须为非空字符串")
        out.append(item.strip())
    return out


def _norm_address(value: object) -> str:
    """地址统一为字符串。接受：字符串、{"line": ..., "city": ...} 对象。"""
    if isinstance(value, str):
        if not value.strip():
            raise _invalid("address", value, "空字符串")
        return value.strip()
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("line", "city", "state", "postalCode", "country")]
        parts = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
        if parts:
            return ", ".join(parts)
        raise _invalid("address", value, "结构化地址无可用部分")
    raise _invalid("address", value, f"不支持的地址类型: {type(value).__name__}")


def _norm_gender(value: object) -> str:
    if isinstance(value, str):
        canonical = _GENDER_ALIASES.get(value.strip().lower())
        if canonical:
            return canonical
    raise _invalid("gender", value, f"无法识别的性别取值: {value}")


_NORMALIZERS = {
    "name": _norm_name,
    "birth_date": _norm_birth_date,
    "phone": _norm_phone,
    "identifier": _norm_identifier,
    "address": _norm_address,
    "gender": _norm_gender,
}


def normalize_patient_fields(raw: dict) -> dict:
    """把来源 Patient 记录的字段名归一到规范名、取值归一到规范格式。

    - 同一规范字段出现多个变体写法 -> 拒绝（invalid_field，语义歧义）；
    - 字段名不在任何已知变体内 -> 拒绝（unsupported_field，绝不原样透传）；
    - 取值无法识别 -> 拒绝（invalid_field）。
    返回以规范字段名为键的 payload。
    """
    payload: dict[str, object] = {}
    for key, value in raw.items():
        canonical = _ALIAS_TO_CANONICAL.get(key)
        if canonical is None:
            raise InteropError(RejectReason.UNSUPPORTED_FIELD,
                               f"无法识别的来源字段: {key}",
                               details={"field": key})
        if canonical in payload:
            raise InteropError(RejectReason.INVALID_FIELD,
                               f"字段 {canonical} 出现多个来源写法，语义歧义",
                               details={"field": canonical, "duplicate_key": key})
        payload[canonical] = _NORMALIZERS[canonical](value)
    return payload
