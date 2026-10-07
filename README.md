# clinical-fhir-interop-python

临床数据互操作服务：以本地存储模拟患者（Patient）与观测（Observation）数据，
使来自不同来源、版本不一致的临床资源在写入、查询与导出时得到一致且可解释的结论。

## 架构

```
main.py            FastAPI 入口，错误原因码 -> HTTP 状态映射
app/
  errors.py        RejectReason 可区分拒绝原因码 + InteropError
  models.py        Coding / Actor / Resource / ResourceVersion 数据模型
  coding.py        编码归一（LOINC/本地体系）与时间归一（UTC）
  normalize.py     Patient 来源字段名归一与取值格式统一
  store.py         VersionedStore：版本链 + 乐观并发 + 幂等键 + 一致性快照
  auth.py          (角色, 用途) -> 动作 的访问控制策略
  masking.py       导出脱敏策略
  audit.py         线程安全审计日志 + 检索过滤与对账汇总
  service.py       业务编排：接入归一、写入、查询、历史、导出、批量导出、审计检索
tests/             畸形输入 / 来源归一 / 并发冲突 / 越权访问 / 批量导出 / 审计检索 用例
```

## 数据模型与编码映射规则

- **Resource**：`resource_type`（Patient/Observation）、`resource_id`、`source`、
  归一化后的 `payload`、统一为 UTC 的 `effective_time`。
- **来源格式归一**：同时接受 camelCase（`resourceType`/`effectiveDateTime`）与
  snake_case（`resource_type`/`effective_time`）字段名。
- **Patient 来源兼容范围**（`app/normalize.py`，写入前统一归一，对外看到的
  字段名与取值格式与来源写法无关）：
  - 字段名变体：`name/patientName/fullName`、`birth_date/birthDate/dateOfBirth/dob`、
    `phone/telecom/phoneNumber/contact/mobile`、`identifier/identifiers/mrn`、
    `address/addr/homeAddress`、`gender/sex/administrativeGender` 等；
  - 取值格式：姓名接受字符串（按空白分词）或 `{"family","given"}` 对象，
    统一为 `{"family": str, "given": [str]}`；出生日期接受纯日期、date/datetime、
    带时区的 ISO 时间（取其书写时的日历日，不做时区换算以免跨日漂移），
    统一为 `YYYY-MM-DD`；电话接受字符串/列表/telecom 对象，统一为去分隔符的
    字符串列表；性别接受 `M/F/男/女` 等别名，统一为 `male/female/other/unknown`；
  - 认不出来的内容一律拒绝，绝不原样透传、绝不静默写入：
    字段名不在已知变体内 -> `unsupported_field`；取值无法解析 -> `invalid_field`；
    同一字段出现多个来源写法（语义歧义）-> `invalid_field`。
  - 由于入库前已归一为规范字段名，研究用途导出的脱敏按规范名命中，
    **无论来源怎么写，身份与出生信息字段一个不漏**，`masked_fields` 清单
    如实反映实际脱敏字段。
- **编码归一**（`app/coding.py`）：
  - 体系别名（`loinc`、`http://loinc.org`、OID 形式）统一映射到 `http://loinc.org`；
  - 本地体系（`local`/`internal`）常见码映射到 LOINC，如 `HR -> 8867-4`、
    `TEMP -> 8310-5`、`SPO2 -> 2708-6`；未映射的合法本地码保留为 `urn:clinical:local`；
  - 格式非法或体系未识别 -> 拒绝（`invalid_code`）。
- **时间归一**：ISO8601（无时区按 UTC 解释）、epoch 秒、日期，统一输出 UTC；
  无法解析 -> 拒绝（`invalid_time`）。
- **拒绝原因码**（均可区分，绝不静默写入）：`missing_field`、`invalid_code`、
  `invalid_time`、`invalid_field`、`unsupported_field`、`unsupported_resource`、
  `version_conflict`、`not_found`、`forbidden`。

## 版本与并发语义

- 每个资源维护完整版本链（`ResourceVersion`），历史版本可通过
  `GET /resources/{type}/{id}/history` 追溯。
- **乐观并发**：写入须携带 `expected_version`（HTTP 头 `X-Expected-Version`，
  新建为 0/缺省）；与当前版本不一致即以 `version_conflict`（HTTP 409）拒绝，
  不会出现覆盖式丢失更新。
- **幂等**：携带 `X-Idempotency-Key` 的重复提交返回首次写入结果
  （`created=false`），不产生新版本，结果稳定。

## 批量导出

- `POST /export/{resource_type}/_batch`，请求体 `{"resource_ids": ["p1", "p2", ...]}`，
  一次导出一批资源，避免逐条往返。
- **部分成功语义**：每个资源独立判定，结果逐项给出
  `{"resource_id", "status": "exported"|"denied", "reason"?,
  "version"?, "payload"?, "masked_fields"?}`，并附
  `summary: {total, exported, denied}` 汇总；不存在的资源记为
  `denied: not_found`，不影响其他资源导出。
- **并发与一致性**：整批资源的版本取自 `VersionedStore.snapshot()` 的同一
  临界区——导出期间即使有并发写入，结果也对应同一个一致时点，
  不会出现有的资源是新值、有的是旧值的混搭，更不会读到半截状态
  （快照之后的写入只影响后续读取，不影响本次导出）。
- 研究用途批量导出同样逐项脱敏；每个资源写一条审计记录，
  审计中的 `masked_fields` 与导出结果一一对应。

## 审计检索

- `GET /audit`，仅 `admin/operations` 角色可用；其他角色返回
  `forbidden`（HTTP 403），与 `not_found` 可区分。
- 查询参数（条件之间为与关系）：`subject`（操作主体）、`action`
  （read/write/export/history/audit_query）、`outcome`（`success` 精确匹配，
  `denied` 前缀匹配所有 `denied:<reason>`）、`start`/`end`（ISO8601 时间范围）。
- 响应含 `records`（命中明细）与 `summary`（`total` + 按动作、按结果的
  分类计数），便于对账。
- 审计记录只保存被脱敏的字段名，绝不保存原始敏感值，因此检索结果
  天然不泄露敏感内容；审计检索本身也会留痕（`audit_query` 动作）。

## 访问控制、脱敏与审计

- 操作主体 `Actor = (subject, role, purpose)`，经 HTTP 头
  `X-Subject` / `X-Role` / `X-Purpose` 传入。
- 策略（`app/auth.py`）：
  - `admin/operations`：全部动作，含审计检索（`audit_query`）；
  - `clinician/treatment`：读、写、导出、历史；
  - `researcher/research`：仅读与导出，不可写、不可查历史、不可检索审计。
  越权返回 `forbidden`（HTTP 403），与资源不存在 `not_found`（HTTP 404）可区分。
- **脱敏**（`app/masking.py`）：研究用途导出时，Patient 的
  `name/identifier/phone/address/birth_date` 与 Observation 的
  `subject_name/note` 替换为 `***MASKED***`；诊疗/运维用途不脱敏。
- **审计**（`app/audit.py`）：每次操作记录主体、角色、用途、动作、资源、
  结果（`success` 或 `denied:<reason>`）与被脱敏字段列表；
  审计只记录被脱敏的字段名，绝不记录原始敏感值。

## 本地验证

```bash
uv sync                 # 安装依赖
uv run pytest -q        # 运行全部测试（35 个用例）
uv run pytest -q -s     # 查看用例日志：输入、参与方与判定依据
uv run python main.py   # 启动服务（127.0.0.1，随机端口）
```

HTTP 调用示例：

```bash
curl -X POST localhost:8000/ingest \
  -H 'X-Subject: dr1' -H 'X-Role: clinician' -H 'X-Purpose: treatment' \
  -H 'X-Source: device-A' -H 'X-Idempotency-Key: k1' \
  -H 'Content-Type: application/json' \
  -d '{"resourceType":"Observation","id":"o1",
       "code":{"system":"local","code":"HR"},
       "effectiveDateTime":"2026-09-27T10:00:00+08:00","value":72}'

# 批量导出（研究用途，自动脱敏）
curl -X POST localhost:8000/export/Patient/_batch \
  -H 'X-Subject: res1' -H 'X-Role: researcher' -H 'X-Purpose: research' \
  -H 'Content-Type: application/json' \
  -d '{"resource_ids":["p1","p2","p3"]}'

# 审计检索（仅运维角色）
curl 'localhost:8000/audit?subject=res1&outcome=denied&start=2026-10-01T00:00:00Z' \
  -H 'X-Subject: ops1' -H 'X-Role: admin' -H 'X-Purpose: operations'
```
