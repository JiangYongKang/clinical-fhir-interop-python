"""来源字段归一：将不同来源的 Patient 字段写法与取值格式统一到规范形态。

原则：
- 字段名兼容 camelCase / snake_case / 常见别名（如 birthDate / dateOfBirth / dob）；
- 取值格式兼容（姓名：字符串或结构化对象；出生日期：纯日期 / 带时区字符串 / epoch；
  联系方式：字符串 / 字符串列表 / telecom 对象列表），统一为规范形态；
- 无法识别的字段名 -> UNSUPPORTED_FIELD（不透传、不静默写入）；
- 取值无法解析 -> INVALID_VALUE；必填缺失 -> MISSING_FIELD；
- 同一规范字段被多个写法同时给出且取值冲突 -> INVALID_VALUE。
"""
from __future__ import annotations

from .coding import normalize_time
from .errors import InteropError, RejectReason

# 规范字段名 -> 可接受的来源字段名变体
_FIELD_VARIANTS: dict[str, tuple[str, ...]] = {
    "name": ("name", "patientName", "patient_name", "fullName", "full_name"),
    "birth_date": ("birth_date", "birthDate", "dateOfBirth", "date_of_birth", "dob"),
    "phone": ("phone", "phoneNumber", "phone_number", "telecom", "contact", "mobile"),
    "identifier": ("identifier", "identifiers", "mrn"),
    "address": ("address", "addr"),
    "gender": ("gender", "sex"),
}

# 姓名结构化对象中可接受的键变体
_FAMILY_KEYS = ("family", "familyName", "family_name", "lastName", "last_name", "surname")
_GIVEN_KEYS = ("given", "givenName", "given_name", "firstName", "first_name")

_GENDER_VALUES = {"male", "female", "other", "unknown"}


def _err(reason: RejectReason, msg: str, **details: object) -> InteropError:
    return InteropError(reason, msg, details=details)


def _normalize_name(value: object) -> dict:
    """姓名：字符串 -> {"text": s}；结构化对象 -> {"family": ..., "given": [...]}。"""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _err(RejectReason.INVALID_VALUE, "姓名不能为空字符串", field="name")
        return {"text": text}
    if isinstance(value, dict):
        unknown = set(value) - set(_FAMILY_KEYS) - set(_GIVEN_KEYS)
        if unknown:
            raise _err(RejectReason.UNSUPPORTED_FIELD,
                       f"姓名对象含无法识别的字段: {sorted(unknown)}",
                       field="name", unknown=sorted(unknown))
        family = next((value[k] for k in _FAMILY_KEYS if k in value), "")
        given_raw = next((value[k] for k in _GIVEN_KEYS if k in value), [])
        if not isinstance(family, str):
            raise _err(RejectReason.INVALID_VALUE, "family 必须是字符串", field="name")
        if isinstance(given_raw, str):
            given = [given_raw]
        elif isinstance(given_raw, list) and all(isinstance(g, str) for g in given_raw):
            given = list(given_raw)
        else:
            raise _err(RejectReason.INVALID_VALUE, "given 必须是字符串或字符串列表",
                       field="name")
        if not family.strip() and not any(g.strip() for g in given):
            raise _err(RejectReason.INVALID_VALUE, "姓名对象缺少有效内容", field="name")
        return {"family": family.strip(), "given": [g.strip() for g in given]}
    raise _err(RejectReason.INVALID_VALUE,
               f"姓名取值格式不支持: {type(value).__name__}", field="name")


def _normalize_birth_date(value: object) -> str:
    """出生日期：纯日期 / 带时区字符串 / epoch -> UTC 日期 'YYYY-MM-DD'。"""
    try:
        dt = normalize_time(value)
    except InteropError as exc:
        raise _err(RejectReason.INVALID_VALUE,
                   f"出生日期无法解析: {value!r}", field="birth_date") from exc
    return dt.date().isoformat()


def _normalize_phone(value: object) -> list[str]:
    """联系方式：字符串 / 字符串列表 / telecom 对象(列表) -> 规范字符串列表。"""
    items: list[object]
    if isinstance(value, (str, dict)):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise _err(RejectReason.INVALID_VALUE,
                   f"联系方式取值格式不支持: {type(value).__name__}", field="phone")
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            entry = item.strip()
        elif isinstance(item, dict) and isinstance(item.get("value"), str):
            entry = item["value"].strip()  # telecom 对象：{"system": "phone", "value": "..."}
        else:
            raise _err(RejectReason.INVALID_VALUE,
                       f"联系方式条目无法识别: {item!r}", field="phone")
        if not entry:
            raise _err(RejectReason.INVALID_VALUE, "联系方式不能为空字符串", field="phone")
        out.append(entry)
    if not out:
        raise _err(RejectReason.INVALID_VALUE, "联系方式列表不能为空", field="phone")
    return out


def _normalize_identifier(value: object) -> list[str]:
    """标识：字符串 / 对象 {"system","value"} / 列表 -> 'system|value' 或 'value' 列表。"""
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            entry = item.strip()
        elif isinstance(item, dict) and isinstance(item.get("value"), str):
            system = item.get("system")
            if system is not None and not isinstance(system, str):
                raise _err(RejectReason.INVALID_VALUE,
                           "identifier.system 必须是字符串", field="identifier")
            entry = f"{system}|{item['value']}" if system else item["value"].strip()
        else:
            raise _err(RejectReason.INVALID_VALUE,
                       f"标识条目无法识别: {item!r}", field="identifier")
        if not entry:
            raise _err(RejectReason.INVALID_VALUE, "标识不能为空字符串", field="identifier")
        out.append(entry)
    if not out:
        raise _err(RejectReason.INVALID_VALUE, "标识列表不能为空", field="identifier")
    return out


def _normalize_address(value: object) -> str:
    """地址：字符串原样归一；对象取 text 或拼接 line/city 等已知键。"""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _err(RejectReason.INVALID_VALUE, "地址不能为空字符串", field="address")
        return text
    if isinstance(value, dict):
        known = ("text", "line", "city", "state", "postalCode", "postal_code", "country")
        unknown = set(value) - set(known)
        if unknown:
            raise _err(RejectReason.UNSUPPORTED_FIELD,
                       f"地址对象含无法识别的字段: {sorted(unknown)}",
                       field="address", unknown=sorted(unknown))
        if isinstance(value.get("text"), str) and value["text"].strip():
            return value["text"].strip()
        parts: list[str] = []
        line = value.get("line")
        lines = line if isinstance(line, list) else ([line] if line else [])
        for seg in [*lines, value.get("city"), value.get("state"),
                    value.get("postalCode") or value.get("postal_code"),
                    value.get("country")]:
            if isinstance(seg, str) and seg.strip():
                parts.append(seg.strip())
            elif seg is not None:
                raise _err(RejectReason.INVALID_VALUE,
                           f"地址组成部分必须是字符串: {seg!r}", field="address")
        if parts:
            return ", ".join(parts)
        raise _err(RejectReason.INVALID_VALUE, "地址对象缺少有效内容", field="address")
    raise _err(RejectReason.INVALID_VALUE,
               f"地址取值格式不支持: {type(value).__name__}", field="address")


def _normalize_gender(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in _GENDER_VALUES:
        return value.strip().lower()
    raise _err(RejectReason.INVALID_VALUE,
               f"性别取值无法识别: {value!r}", field="gender")


_NORMALIZERS = {
    "name": _normalize_name,
    "birth_date": _normalize_birth_date,
    "phone": _normalize_phone,
    "identifier": _normalize_identifier,
    "address": _normalize_address,
    "gender": _normalize_gender,
}


def normalize_patient_payload(raw: dict) -> dict:
    """将来源 Patient 记录（任意字段写法）归一为规范 payload。

    返回的 payload 只含规范字段名与规范取值；任何无法识别的字段或取值
    都以可区分原因拒绝，绝不透传、绝不静默丢弃。
    """
    # 1) 字段名归一：来源写法 -> 规范字段；同一规范字段冲突即拒绝
    collected: dict[str, object] = {}
    for key, value in raw.items():
        canonical = next((c for c, variants in _FIELD_VARIANTS.items() if key in variants),
                         None)
        if canonical is None:
            raise _err(RejectReason.UNSUPPORTED_FIELD,
                       f"无法识别的 Patient 字段: {key}", field=key)
        if canonical in collected and collected[canonical] != value:
            raise _err(RejectReason.INVALID_VALUE,
                       f"字段 {canonical} 被多个来源写法给出且取值冲突",
                       field=canonical)
        collected[canonical] = value

    # 2) 必填校验
    if "name" not in collected:
        raise _err(RejectReason.MISSING_FIELD, "Patient 缺少必填字段: name", field="name")

    # 3) 取值归一
    return {canonical: _NORMALIZERS[canonical](value)
            for canonical, value in collected.items()}
