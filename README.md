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
  store.py         VersionedStore：版本链 + 乐观并发 + 幂等键
  auth.py          (角色, 用途) -> 动作 的访问控制策略
  masking.py       导出脱敏策略
  audit.py         线程安全审计日志
  service.py       业务编排：接入归一、写入、查询、历史、导出
tests/             畸形输入 / 并发冲突 / 越权访问 / 脱敏审计 用例
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
  `invalid_time`、`unsupported_resource`、`version_conflict`、`not_found`、`forbidden`。

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
uv run pytest -q        # 运行全部测试（18 个用例）
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
