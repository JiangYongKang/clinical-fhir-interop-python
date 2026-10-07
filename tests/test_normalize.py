"""来源写法归一：字段名变体与取值格式统一，认不出来的按可区分原因拒绝；
无论来源怎么写，研究用途导出都必须脱敏且清单完整。"""
import pytest

from app.errors import InteropError, RejectReason
from app.masking import MASK
from tests.conftest import log_case

# 同一患者，三种来源写法：camelCase + 结构化姓名、snake_case + 字符串姓名、
# 设备缩写（dob/mrn/telecom）+ 带时区出生时间
SRC_CAMEL = {"resourceType": "Patient", "id": "p-a",
             "patientName": {"family": "Zhang", "given": "San"},
             "birthDate": "1990-05-01", "phoneNumber": "138-0000-1111",
             "gender": "M"}
SRC_SNAKE = {"resource_type": "Patient", "id": "p-b",
             "full_name": "Zhang San", "birth_date": "1990-05-01",
             "phone": ["13800001111", "010-1234567"], "sex": "male"}
SRC_DEVICE = {"resource_type": "Patient", "id": "p-c",
              "name": "Zhang San",
              "dob": "1990-05-01T00:00:00+08:00",
              "mrn": "MRN-001",
              "telecom": [{"system": "phone", "value": "+86 138 0000 1111"}],
              "address": {"line": "1 Main St", "city": "Beijing"}}


def test_camelcase_structured_name_normalized(svc, clinician):
    log_case("norm-camel", inputs=SRC_CAMEL, parties=[clinician.subject],
             rationale="camelCase 字段名与结构化姓名应归一为规范名与规范结构；M->male；电话去分隔符")
    out = svc.ingest(clinician, SRC_CAMEL, source="his-A")
    p = out["normalized"]
    assert p["name"] == {"family": "Zhang", "given": ["San"]}
    assert p["birth_date"] == "1990-05-01"
    assert p["phone"] == ["13800001111"]
    assert p["gender"] == "male"


def test_string_name_and_list_phone_normalized(svc, clinician):
    log_case("norm-snake", inputs=SRC_SNAKE, parties=[clinician.subject],
             rationale="字符串姓名按空白分词为 family/given；电话列表逐项归一；sex->gender")
    out = svc.ingest(clinician, SRC_SNAKE, source="his-B")
    p = out["normalized"]
    assert p["name"] == {"family": "San", "given": ["Zhang"]}
    assert p["phone"] == ["13800001111", "0101234567"]
    assert p["gender"] == "male"


def test_device_abbrev_and_tz_birthtime_normalized(svc, clinician):
    log_case("norm-device", inputs=SRC_DEVICE, parties=[clinician.subject],
             rationale="dob/mrn/telecom 等缩写应识别；带时区出生时间取其日历日；telecom 对象取值")
    out = svc.ingest(clinician, SRC_DEVICE, source="device-C")
    p = out["normalized"]
    assert p["name"] == {"family": "San", "given": ["Zhang"]}
    assert p["birth_date"] == "1990-05-01"          # 取书写时的日历日，不做时区换算
    assert p["identifier"] == ["MRN-001"]
    assert p["phone"] == ["+8613800001111"]
    assert p["address"] == "1 Main St, Beijing"


def test_unknown_field_rejected_not_passed_through(svc, clinician):
    bad = {"resource_type": "Patient", "id": "p-x", "name": "Li Si",
           "someUnknownField": "raw-value"}
    log_case("norm-unknown-field", inputs=bad, parties=[clinician.subject],
             rationale="认不出的字段名须以 unsupported_field 拒绝，不得原样透传或静默写入")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="his-A")
    assert ei.value.reason == RejectReason.UNSUPPORTED_FIELD
    from app.models import ResourceType
    assert svc.store.get_current(ResourceType.PATIENT, "p-x") is None


def test_invalid_birth_date_rejected(svc, clinician):
    bad = {"resource_type": "Patient", "id": "p-x", "name": "Li Si",
           "birthDate": "not-a-date"}
    log_case("norm-bad-birthdate", inputs=bad, parties=[clinician.subject],
             rationale="出生日期无法解析须以 invalid_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="his-A")
    assert ei.value.reason == RejectReason.INVALID_FIELD
    assert ei.value.details["field"] == "birth_date"


def test_invalid_phone_rejected(svc, clinician):
    bad = {"resource_type": "Patient", "id": "p-x", "name": "Li Si",
           "phone": "abc"}
    log_case("norm-bad-phone", inputs=bad, parties=[clinician.subject],
             rationale="电话号码非法须以 invalid_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="his-A")
    assert ei.value.reason == RejectReason.INVALID_FIELD


def test_conflicting_aliases_rejected(svc, clinician):
    bad = {"resource_type": "Patient", "id": "p-x",
           "name": "Li Si", "fullName": "Si Li"}
    log_case("norm-alias-conflict", inputs=bad, parties=[clinician.subject],
             rationale="同一字段多个来源写法语义歧义，须以 invalid_field 拒绝")
    with pytest.raises(InteropError) as ei:
        svc.ingest(clinician, bad, source="his-A")
    assert ei.value.reason == RejectReason.INVALID_FIELD


def test_masking_covers_all_source_spellings(svc, clinician, researcher):
    """三种写法写入的身份/出生信息，研究导出必须全部脱敏且清单如实完整。"""
    svc.ingest(clinician, SRC_CAMEL, source="his-A")
    svc.ingest(clinician, SRC_SNAKE, source="his-B")
    svc.ingest(clinician, SRC_DEVICE, source="device-C")
    log_case("norm-mask-coverage",
             inputs={"ids": ["p-a", "p-b", "p-c"]},
             parties=[researcher.subject],
             rationale="不同来源写法的 name/birth_date/phone/identifier/address 均须脱敏，"
                       "masked_fields 须如实反映且一个不漏")
    expectations = {
        "p-a": {"name", "birth_date", "phone"},
        "p-b": {"name", "birth_date", "phone"},
        "p-c": {"name", "birth_date", "phone", "identifier", "address"},
    }
    for pid, expected_masked in expectations.items():
        out = svc.export(researcher, "Patient", pid)
        assert set(out["masked_fields"]) == expected_masked
        for f in expected_masked:
            assert out["payload"][f] == MASK
        # 原始敏感值不得出现在导出结果中
        blob = repr(out["payload"])
        for raw in ("Zhang", "1990-05-01", "13800001111", "MRN-001", "Main St"):
            assert raw not in blob
    # 审计中的脱敏清单与导出结果一一对应
    export_recs = [r for r in svc.audit.all()
                   if r.action == "export" and r.outcome == "success"]
    assert len(export_recs) == 3
    for rec in export_recs:
        assert set(rec.masked_fields) == expectations[rec.resource_id]
