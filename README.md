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
  normalize.py     Patient 来源字段归一（字段名变体 + 取值格式统一）
  store.py         VersionedStore：版本链 + 乐观并发 + 幂等键 + 一致性快照
  auth.py          (角色, 用途) -> 动作 的访问控制策略
  masking.py       导出脱敏策略
  audit.py         线程安全审计日志 + 检索与汇总
  service.py       业务编排：接入归一、写入、查询、历史、单条/批量导出、审计检索
tests/             畸形输入 / 来源写法归一 / 并发冲突 / 批量导出一致性 /
                   越权访问 / 脱敏审计 / 审计检索 用例
```

## 数据模型与编码映射规则

- **Resource**：`resource_type`（Patient/Observation）、`resource_id`、`source`、
  归一化后的 `payload`、统一为 UTC 的 `effective_time`。
- **来源格式归一**：同时接受 camelCase（`resourceType`/`effectiveDateTime`）与
  snake_case（`resource_type`/`effective_time`）字段名。
- **编码归一**（`app/coding.py`）：
  - 体系别名（`loinc`、`http://loinc.org`、OID 形式）统一映射到 `http://loinc.org`；
  - 本地体系（`local`/`internal`）常见码映射到 LOINC，如 `HR -> 8867-4`、
    `TEMP -> 8310-5`、`SPO2 -> 2708-6`；未映射的合法本地码保留为 `urn:clinical:local`；
  - 格式非法或体系未识别 -> 拒绝（`invalid_code`）。
- **时间归一**：ISO8601（无时区按 UTC 解释）、epoch 秒、日期，统一输出 UTC；
  无法解析 -> 拒绝（`invalid_time`）。
- **拒绝原因码**（均可区分，绝不静默写入）：`missing_field`、`invalid_code`、
  `invalid_time`、`invalid_value`、`unsupported_field`、`unsupported_resource`、
  `version_conflict`、`not_found`、`forbidden`。

## Patient 来源兼容范围（`app/normalize.py`）

不同来源的字段写法与取值格式在写入时统一为规范形态；无法识别的一律按
可区分原因拒绝，**不透传、不静默写入**。

- **字段名变体**（同一规范字段的多种写法均可接受）：
  - `name`：`name` / `patientName` / `patient_name` / `fullName` / `full_name`
  - `birth_date`：`birth_date` / `birthDate` / `dateOfBirth` / `date_of_birth` / `dob`
  - `phone`：`phone` / `phoneNumber` / `phone_number` / `telecom` / `contact` / `mobile`
  - `identifier`：`identifier` / `identifiers` / `mrn`
  - `address`：`address` / `addr`；`gender`：`gender` / `sex`
  - 不在表内的字段名 -> `unsupported_field` 拒绝；
    同一字段被多种写法给出且取值冲突 -> `invalid_value` 拒绝。
- **取值格式**：
  - 姓名：字符串 -> `{"text": ...}`；结构化对象（`family/familyName/lastName/surname`、
    `given/givenName/firstName`，given 可为字符串或列表）->
    `{"family": ..., "given": [...]}`；
  - 出生日期：纯日期 / 带时区 ISO8601 / epoch 秒 -> UTC 日期 `YYYY-MM-DD`；
  - 联系方式：字符串 / 字符串列表 / telecom 对象（`{"system","value"}`）-> 字符串列表；
  - 标识：字符串 / `{"system","value"}` 对象（或列表）-> `system|value` 或 `value` 列表；
  - 地址：字符串或含 `text/line/city/state/postalCode/country` 的对象 -> 规范字符串；
  - 性别：`male/female/other/unknown`（大小写不敏感）-> 小写。
  - 取值无法解析 -> `invalid_value` 拒绝；`name` 缺失 -> `missing_field` 拒绝。
- **脱敏覆盖**：由于写入前已归一为规范字段名，患者身份与出生信息
  （`name/identifier/phone/address/birth_date`）无论来源怎么写，
  研究用途导出时全部脱敏，`masked_fields` 清单如实列出每一个被脱字段。

## 批量导出（`POST /export/{resource_type}/batch`）

请求体 `{"resource_ids": ["p-1", "p-2", ...]}`，一次调用导出一批资源：

- **逐项结论**：每个 id 独立给出 `status: ok`（含 `version`、脱敏后
  `payload`、`masked_fields`）或 `status: rejected` + `reason`
  （如 `not_found`），部分失败不影响其他项；`summary` 给出
  total/ok/rejected 计数。
- **一致性语义**：整批资源在同一把锁内一次性读取（`VersionedStore.snapshot`），
  结果对应同一个时间切面的完整版本——并发写入要么整体先于快照
  （新版本可见）、要么整体后于快照（不可见），不会出现同一批结果里
  有的资源是新值、有的是旧值，也不会出现半截状态。
- **脱敏与审计对齐**：研究用途照常脱敏；每个 id 产生一条审计记录，
  其 `masked_fields` 与导出结果逐项一一对应，拒绝项记 `denied:<reason>`。
- 无导出权限的主体整体以 `forbidden` 拒绝；空 id 列表以 `missing_field` 拒绝。

## 审计检索与汇总（`GET /audit`、`GET /audit/summary`）

- **仅限运维角色**（`admin/operations`）；其他角色查询以 `forbidden`
  可区分地拒绝（HTTP 403）。
- 查询参数均可选、可组合：`subject`（操作主体）、`action`
  （read/write/export/history/audit_query）、`outcome`
  （`success`、`denied` 匹配所有 `denied:*`，或精确值如 `denied:not_found`）、
  `since` / `until`（ISO8601 时间范围）。
- `/audit` 返回匹配记录；`/audit/summary` 返回汇总（按动作/结果计数、
  脱敏字段事件总数），便于对账。
- 检索结果只含主体、动作、结果与被脱敏**字段名**，绝不携带原始敏感值；
  审计查询本身也会被记录（`audit_query` 动作）。


## 版本与并发语义

- 每个资源维护完整版本链（`ResourceVersion`），历史版本可通过
  `GET /resources/{type}/{id}/history` 追溯。
- **乐观并发**：写入须携带 `expected_version`（HTTP 头 `X-Expected-Version`，
  新建为 0/缺省）；与当前版本不一致即以 `version_conflict`（HTTP 409）拒绝，
  不会出现覆盖式丢失更新。
- **幂等**：携带 `X-Idempotency-Key` 的重复提交返回首次写入结果
  （`created=false`），不产生新版本，结果稳定。

## 访问控制、脱敏与审计

- 操作主体 `Actor = (subject, role, purpose)`，经 HTTP 头
  `X-Subject` / `X-Role` / `X-Purpose` 传入。
- 策略（`app/auth.py`）：
  - `admin/operations`：全部动作；
  - `clinician/treatment`：读、写、导出、历史；
  - `researcher/research`：仅读与导出，不可写、不可查历史。
  审计检索（`audit_query`）仅 `admin/operations` 可用。
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
uv run pytest -q        # 运行全部测试（34 个用例）
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
```

批量导出与审计检索示例：

```bash
# 批量导出（研究用途，自动脱敏，逐项给出成功/拒绝及原因）
curl -X POST localhost:8000/export/Patient/batch \
  -H 'X-Subject: res1' -H 'X-Role: researcher' -H 'X-Purpose: research' \
  -H 'Content-Type: application/json' \
  -d '{"resource_ids":["p-1","p-2","p-3"]}'

# 审计检索与汇总（仅运维角色）
curl 'localhost:8000/audit?subject=res1&action=export&outcome=success&since=2026-10-01' \
  -H 'X-Subject: ops1' -H 'X-Role: admin' -H 'X-Purpose: operations'
curl 'localhost:8000/audit/summary?outcome=denied' \
  -H 'X-Subject: ops1' -H 'X-Role: admin' -H 'X-Purpose: operations'
```
