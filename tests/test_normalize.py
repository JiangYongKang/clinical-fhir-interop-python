"""来源写法归一：不同来源的字段名/取值格式统一，无法识别的可区分拒绝，
且身份与出生信息无论来源怎么写，研究导出都必须脱敏、清单完整。"""
import pytest

from app.errors import InteropError, RejectReason
from app.masking import MASK
from tests.conftest import log_case


def _ingest_patient(svc, actor, extra, rid="p-1"):
    raw = {"resourceType": "Patient", "id": rid, **extra}
    return svc.ingest(actor, raw, source="src-x")


def test_field_name_variants_unified(svc, clinician):
    """camelCase / snake_case / 别名写法归一后对外完全一致。"""
    log_case("field-variants",
             inputs={"a": {"fullName": "Zhang San", "dob": "1980-05-01"},
                     "b": {"patient_name": "Zhang San", "birthDate": "1980-05-01"},
                     "c": {"name": "Zhang San", "birth_date": "1980-05-01"}},
             parties=[clinician.subject],
             rationale="三种写法归一后的规范 payload 必须逐字段相同")
    outs = []
    for i, extra in enumerate([
            {"fullName": "Zhang San", "dob": "1980-05-01"},
            {"patient_name": "Zhang San", "birthDate": "1980-05-01"},
            {"name": "Zhang San", "birth_date": "1980-05-01"}]):
        outs.append(_ingest_patient(svc, clinician, extra, rid=f"p-{i}")["normalized"])
    assert outs[0] == outs[1] == outs[2]
    assert outs[0]["name"] == {"text": "Zhang San"}
    assert outs[0]["birth_date"] == "1980-05-01"


def test_value_format_variants_unified(svc, clinician):
    """姓名（字符串/结构化）、出生日期（纯日期/带时区/epoch）、
    联系方式（字符串/telecom 列表）归一后取值格式统一。"""
    log_case("value-variants",
             inputs={"name": ["Zhang San", {"familyName": "Zhang", "givenName": ["San"]}],
                     "birth_date": ["1980-05-01", "1980-05-01T08:00:00+08:00", 325987200],
                     "phone": ["138", [{"system": "phone", "value": "138"}]]},
             parties=[clinician.subject],
             rationale="合法取值一律归一为规范形态：结构化姓名 / UTC 日期 / 字符串列表")
    a = _ingest_patient(svc, clinician, {"name": {"familyName": "Zhang", "givenName": ["San"]},
                                         "birthDate": "1980-05-01T08:00:00+08:00",
                                         "telecom": [{"system": "phone", "value": "138"}]},
                        rid="p-a")["normalized"]
    b = _ingest_patient(svc, clinician, {"name": {"family": "Zhang", "given": "San"},
                                         "birth_date": "1980-05-01",
                                         "phone": "138"},
                        rid="p-b")["normalized"]
    assert a == b
    assert a["name"] == {"family": "Zhang", "given": ["San"]}
    assert a["birth_date"] == "1980-05-01"  # 带时区时间归一为 UTC 日期
    assert a["phone"] == ["138"]
    c = _ingest_patient(svc, clinician, {"name": "X", "dob": 325987200}, rid="p-c")
    assert c["normalized"]["birth_date"] == "1980-05-01"  # epoch 秒同样归一


def test_unknown_field_rejected_not_passed_through(svc, clinician):
    log_case("unknown-field", inputs={"nickname": "小三"},
             parties=[clinician.subject],
             rationale="无法识别的字段须以 unsupported_field 拒绝，不透传不静默写入")
    with pytest.raises(InteropError) as ei:
        _ingest_patient(svc, clinician, {"name": "Zhang", "nickname": "小三"})
    assert ei.value.reason == RejectReason.UNSUPPORTED_FIELD
    from app.models import ResourceType
    assert svc.store.get_current(ResourceType.PATIENT, "p-1") is None


def test_unparseable_birth_date_rejected(svc, clinician):
    log_case("bad-birth-date", inputs={"dob": "明年五月"},
             parties=[clinician.subject],
             rationale="出生日期无法解析须以 invalid_value 拒绝")
    with pytest.raises(InteropError) as ei:
        _ingest_patient(svc, clinician, {"name": "Zhang", "dob": "明年五月"})
    assert ei.value.reason == RejectReason.INVALID_VALUE
    assert ei.value.details.get("field") == "birth_date"


def test_conflicting_variants_rejected(svc, clinician):
    log_case("conflict", inputs={"birthDate": "1980-05-01", "dob": "1990-01-01"},
             parties=[clinician.subject],
             rationale="同一字段多种写法取值冲突须以 invalid_value 拒绝")
    with pytest.raises(InteropError) as ei:
        _ingest_patient(svc, clinician,
                        {"name": "Zhang", "birthDate": "1980-05-01", "dob": "1990-01-01"})
    assert ei.value.reason == RejectReason.INVALID_VALUE


def test_missing_name_rejected(svc, clinician):
    log_case("missing-name", inputs={"birthDate": "1980-05-01"},
             parties=[clinician.subject],
             rationale="Patient 缺少 name 须以 missing_field 拒绝")
    with pytest.raises(InteropError) as ei:
        _ingest_patient(svc, clinician, {"birthDate": "1980-05-01"})
    assert ei.value.reason == RejectReason.MISSING_FIELD


def test_masking_covers_all_source_spellings(svc, clinician, researcher):
    """身份/出生信息无论来源怎么写，研究导出全部脱敏且清单完整。"""
    extra = {"fullName": "Zhang San", "dob": "1980-05-01T00:30:00+08:00",
             "telecom": [{"system": "phone", "value": "13800001111"}],
             "mrn": "MRN-1", "addr": "Beijing", "sex": "Male"}
    log_case("mask-all-spellings", inputs=extra,
             parties=[clinician.subject, researcher.subject],
             rationale="name/birth_date/phone/identifier/address 全脱敏，"
                       "gender 非敏感保留；masked_fields 一个不能漏")
    _ingest_patient(svc, clinician, extra)
    out = svc.export(researcher, "Patient", "p-1")
    assert set(out["masked_fields"]) == {"name", "birth_date", "phone",
                                         "identifier", "address"}
    for f in out["masked_fields"]:
        assert out["payload"][f] == MASK
    assert out["payload"]["gender"] == "male"
    # 原始敏感值不得出现在导出结果中
    blob = str(out)
    for raw in ("Zhang", "13800001111", "MRN-1", "Beijing", "1980"):
        assert raw not in blob
