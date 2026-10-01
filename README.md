# 铁路区间临时限速编排服务

使用 **Python 3.12 + FastAPI + PostgreSQL 16** 实现。服务提供 HTTP API，用于录入线路基础限速、创建/发布/撤销临时限速命令，并查询指定 UTC 时刻全线各里程区间的最终限速。

## 1. 核心规则

- 线路总里程必须大于 0。
- 基础限速段：
  - 从里程 `0` 开始，连续、无缝、无重叠地覆盖到线路总里程；
  - 所有里程、限速均为正数（起点允许为 0）；
  - 录入后不可修改、不可删除；
  - 数据库额外使用触发器和 GiST 排他约束进行防护。
- 临时命令具有唯一编号、里程范围、限速值和 UTC 生效时段。
- 里程和时间区间均为 **左闭右开**：`[start, end)`。
- 命令创建后为 `draft`；草稿不参与查询，只有发布后的命令才可能参与查询。
- 发布使用数据库事务时间；生效起点不能早于发布时的服务端时间，因此不能回溯生效，允许与发布时刻相同而立即生效。
- 撤销在服务端当前事务时间原子生效；撤销时刻起命令不再参与查询，历史时刻的查询结果保持可重现。
- 发布和撤销必须提交当前命令版本 `expectedVersion`，服务端行锁 + 条件更新保证版本校验和状态变更是原子操作。
- 查询时按基础段边界、临时命令边界统一切段；重叠命令与基础限速比较，取最低限速。
- `commandNumbers` 只包含达到最终最低限速的临时命令编号；若最低值来自基础限速，则为空数组。
- 相邻切段仅在 **限速相同且命令编号集合也相同** 时合并。

## 2. 一键启动

要求安装 Docker 和 Docker Compose（支持 `docker compose` 子命令）。

```bash
docker compose up --build
```

启动后：

- API 地址：<http://localhost:8000>
- Swagger UI：<http://localhost:8000/docs>
- OpenAPI：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/health>
- PostgreSQL：`localhost:5432`

应用启动时会自动创建表、索引、PostgreSQL 扩展和约束，不需要手工执行迁移。

停止并删除容器：

```bash
docker compose down
```

同时删除数据库数据卷：

```bash
docker compose down -v
```

## 3. 配置

唯一必需配置是数据库连接串，在 `docker-compose.yml` 中已设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://railway:railway@db:5432/railway` | PostgreSQL SQLAlchemy 连接串 |

本地非容器方式启动示例：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg2://railway:railway@localhost:5432/railway'
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 4. API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/line` | 一次性录入总里程和基础限速段 |
| `GET` | `/line` | 查询线路和基础限速段 |
| `POST` | `/commands` | 创建草稿临时命令 |
| `GET` | `/commands` | 列出全部命令及当前版本/状态 |
| `GET` | `/commands/{number}` | 查询单个命令 |
| `POST` | `/commands/{number}/publish` | 校验版本并发布 |
| `POST` | `/commands/{number}/cancel` | 校验版本并撤销 |
| `GET` | `/query?at=...` | 查询指定 UTC 时刻限速 |
| `POST` | `/query` | 使用 JSON Body 查询指定 UTC 时刻限速 |

错误状态：

- `404`：命令不存在；
- `409`：线路已存在、线路未初始化、重复编号、状态冲突或版本冲突；
- `422`：请求字段、里程范围、时间范围或业务约束不合法。

## 5. 调用示例

以下示例使用固定的 2027 年 UTC 时间。实际发布时，请把 `effectiveStart` 设置为**不早于当前服务端时间**的时间。

### 5.1 初始化线路

线路总里程 `100`，基础限速段为：

- `[0, 50)`：120
- `[50, 100)`：100

```bash
curl -sS -X POST http://localhost:8000/line \
  -H 'Content-Type: application/json' \
  -d '{
    "totalMileage": 100,
    "baseSegments": [
      {"startMileage": 0, "endMileage": 50, "speed": 120},
      {"startMileage": 50, "endMileage": 100, "speed": 100}
    ]
  }'
```

成功响应：

```json
{
  "total_mileage": "100",
  "base_segments": [
    {"start_mileage": "0", "end_mileage": "50", "speed": "120", "command_numbers": []},
    {"start_mileage": "50", "end_mileage": "100", "speed": "100", "command_numbers": []}
  ]
}
```

### 5.2 创建临时命令

创建 `C-100`：范围 `[10, 60)`，限速 80，UTC 时段 `[2027-01-01T01:00Z, 2027-01-01T03:00Z)`。

```bash
curl -sS -X POST http://localhost:8000/commands \
  -H 'Content-Type: application/json' \
  -d '{
    "number": "C-100",
    "startMileage": 10,
    "endMileage": 60,
    "speed": 80,
    "effectiveStart": "2027-01-01T01:00:00Z",
    "effectiveEnd": "2027-01-01T03:00:00Z"
  }'
```

再创建 `C-101`：范围 `[40, 70)`，限速 80，时段 `[2027-01-01T01:00Z, 2027-01-01T02:00Z)`。

```bash
curl -sS -X POST http://localhost:8000/commands \
  -H 'Content-Type: application/json' \
  -d '{
    "number": "C-101",
    "startMileage": 40,
    "endMileage": 70,
    "speed": 80,
    "effectiveStart": "2027-01-01T01:00:00Z",
    "effectiveEnd": "2027-01-01T02:00:00Z"
  }'
```

新命令初始状态为 `draft`、版本为 `0`，不会参与查询。

### 5.3 发布命令

发布必须携带创建响应中的当前版本，草稿版本为 `0`。发布成功后版本变为 `1`。

```bash
curl -sS -X POST http://localhost:8000/commands/C-100/publish \
  -H 'Content-Type: application/json' \
  -d '{"expectedVersion": 0}'

curl -sS -X POST http://localhost:8000/commands/C-101/publish \
  -H 'Content-Type: application/json' \
  -d '{"expectedVersion": 0}'
```

如果生效起点早于服务端当前时间，会返回 `422`，拒绝回溯发布。并发请求若再次提交版本 `0`，后提交者会收到 `409` 版本冲突。

### 5.4 查询 UTC 时刻

```bash
curl -sS --get 'http://localhost:8000/query' \
  --data-urlencode 'at=2027-01-01T01:30:00Z'
```

等价 POST：

```bash
curl -sS -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"at": "2027-01-01T01:30:00Z"}'
```

响应：

```json
{
  "at": "2027-01-01T01:30:00Z",
  "total_mileage": "100",
  "segments": [
    {"start_mileage": "0", "end_mileage": "10", "speed": "120", "command_numbers": []},
    {"start_mileage": "10", "end_mileage": "40", "speed": "80", "command_numbers": ["C-100"]},
    {"start_mileage": "40", "end_mileage": "60", "speed": "80", "command_numbers": ["C-100", "C-101"]},
    {"start_mileage": "60", "end_mileage": "70", "speed": "80", "command_numbers": ["C-101"]},
    {"start_mileage": "70", "end_mileage": "100", "speed": "100", "command_numbers": []}
  ]
}
```

`[10,40)` 和 `[40,60)` 虽然限速都为 80，但命令编号集合不同，因此不会合并。

在 `2027-01-01T02:00:00Z`，由于时间左闭右开，`C-101` 已不再生效：

```bash
curl -sS --get 'http://localhost:8000/query' \
  --data-urlencode 'at=2027-01-01T02:00:00Z'
```

### 5.5 撤销命令

`C-100` 发布成功后的版本为 `1`。撤销必须携带版本 `1`，撤销成功后版本变为 `2`。

```bash
curl -sS -X POST http://localhost:8000/commands/C-100/cancel \
  -H 'Content-Type: application/json' \
  -d '{"expectedVersion": 1}'
```

撤销按服务端数据库时间生效：

- 查询撤销前的历史 UTC 时刻，仍能得到与当时一致的结果；
- 查询撤销时刻及之后，命令不再生效。

## 6. 数据模型摘要

- `lines`：单行线路表，触发器禁止更新和删除。
- `base_segments`：基础限速段，外键关联线路；GiST 排他约束防止同线里程重叠。
- `commands`：命令主表，保存编号、范围、速度、UTC 生效时段、状态、版本、发布时间和撤销时间。

命令状态：

1. `draft`：已创建但未发布，不参与查询；
2. `published`：已发布且当前未撤销，在生效时段内参与查询；
3. `cancelled`：已发布后撤销；查询撤销时刻之前的历史时刻仍生效，查询撤销时刻及之后不生效。

## 7. 本地检查

纯切段逻辑位于 `app/service.py`，无需数据库即可运行：

```bash
python3 -m unittest discover -s tests
```

完整 API 可在容器启动后通过 Swagger UI 验证。构建容器镜像前，Docker Compose 会等待 PostgreSQL 健康检查通过，再启动 API。
