# Clinical FHIR Interop Service (Python Bootstrap)

一套基于本地存储的临床数据互操作服务：来自不同来源、版本不一致的临床资源
（Patient / Observation）在写入、查询与导出时得到一致且可解释的结论。

## 结构

```
app/
  errors.py     # 类型化拒绝原因（RejectReason / Rejected）
  models.py     # 规范化内部模型与版本化资源记录
  normalize.py  # 编码映射、时间语义统一、字段校验
  store.py      # 本地版本化存储：乐观并发 + 幂等 + 历史
  security.py   # 角色/用途访问控制与导出脱敏策略
  audit.py      # 只追加审计日志（不含原始敏感值）
  service.py    # 编排层：鉴权 -> 归一 -> 存储 -> 审计
  api.py        # FastAPI 接口，拒绝原因映射为可区分的 HTTP 状态码
tests/          # 畸形输入、重复提交、并发冲突、越权访问测试
```

## 数据模型与编码映射规则

- 内部模型（`app/models.py`）：`Patient`、`Observation`，所有时间统一为
  UTC ISO-8601（`...Z`），所有编码归入约定体系 `loinc` / `snomed` / `icd10`。
- 编码映射（`app/normalize.py`）：
  - 系统别名归一：如 `SNOMED-CT`、`http://snomed.info/sct`、`sct` → `snomed`；
    `http://loinc.org`、`ln` → `loinc`；`icd-10` → `icd10`。
  - 本地字典校验：未知 code 拒绝（`invalid_coding`），绝不静默写入。
  - 跨系统等价：Observation 统一映射到 LOINC（如 SNOMED `271649006` →
    LOINC `8480-6`）。
  - 单位别名归一：`mmHg` → `mm[Hg]`，`mg/dl` → `mg/dL`。
- 时间语义：带偏移的 `dateTime` 转 UTC；裸日期视为 UTC 零点；朴素时间可配
  `timezone` 提示；无法解析即拒绝（`invalid_time`）。
- 字段缺失（`missing_field`）、非法编码（`invalid_coding`）、非法时间
  （`invalid_time`）、不支持的资源类型（`unsupported_resource`）分别以可区分
  的原因拒绝。

## 版本与并发语义

- 每次写入必须携带 `base_version`（新建为 `null`）。与当前版本不一致时以
  `version_conflict`（HTTP 409）拒绝——不存在覆盖式丢失更新。
- 每次写入必须携带 `idempotency_key`：相同键 + 相同请求重放返回原结果
  （版本不变，结果稳定）；相同键 + 不同请求以 `version_conflict` 拒绝。
- 每次成功写入追加历史版本，`GET .../history` 可追溯全部版本。
- 存储层以单锁保证并发下"检查版本 + 应用写入"的原子性；并发写入同一版本
  时恰有一个成功，其余全部收到 `version_conflict`。

## 访问控制、脱敏与审计

- 访问矩阵（`app/security.py`）：

  | 动作    | clinician | researcher | admin |
  |---------|-----------|------------|-------|
  | read    | ✓         | ✓          | ✓     |
  | write   | ✓         | ✗          | ✓     |
  | export  | ✓         | ✓          | ✗     |
  | history | ✓         | ✗          | ✓     |

- 越权拒绝为 `forbidden`（HTTP 403），与资源不存在 `not_found`（404）可区分。
- 导出脱敏：仅 `clinician + treatment` 可见敏感字段（family/given 姓名、
  birth_date、mrn），其余导出以 `***` 替换，并在响应与审计中记录被脱敏的
  字段名。
- 审计（`app/audit.py`）：记录操作主体、角色、用途、动作、资源、判定
  （allow/deny）、拒绝原因与被脱敏字段名；**绝不记录原始敏感值**。

## 本地验证

```bash
# 运行全部测试（畸形输入 / 重复提交 / 并发冲突 / 越权访问）
python3 -m pytest tests/ -q

# 查看单测日志中的输入、参与方与判定依据
python3 -m pytest tests/ -q --log-cli-level=INFO

# 启动服务
python3 main.py   # 或 uvicorn main:app
```

HTTP 调用示例（以请求头声明身份）：

```bash
curl -X PUT localhost:8000/resources/Patient/p1 \
  -H 'X-Subject: dr.chen' -H 'X-Role: clinician' -H 'X-Purpose: treatment' \
  -H 'Content-Type: application/json' \
  -d '{"payload": {"id":"p1","family":"Wang","given":"Fang","birthDate":"1980-05-01","mrn":"M1"},
       "idempotency_key": "req-1"}'
```
