# 铁路区间临时限速编排服务

基于 **Python (FastAPI) + PostgreSQL** 的铁路区间临时限速编排 HTTP 服务。
录入线路总里程与基础限速段，登记/发布/撤销带唯一编号的临时限速命令，
并按指定 UTC 时刻查询全线限速分段。里程区间与时间区间一律 **左闭右开**。

## 核心规则

### 录入（一次性，不可改）

* 线路总里程必须为正数。
* 基础限速段必须 **连续覆盖 `[0, total_km)` 全线**、互不重叠，段限速为正数。
* 基础段录入后不可修改、不可重复录入（数据库另有 GiST 排他约束兜底）。

### 临时命令生命周期

* 命令含唯一编号、里程范围（越界/空/逆序一律拒绝）、正限速值、UTC 生效时段
  `[valid_from, valid_to)`。
* 创建后为 `draft`（草稿），**仅 `published` 命令参与查询**。
* **发布不得回溯生效**：`valid_from` 必须晚于发布事务的服务端时间（DB `now()`）。
* **撤销按服务端时间生效**：记录 `revoked_at = now()`；撤销之后的时刻不再命中该命令，
  撤销之前的历史时刻查询结果保持可重现。
* 发布、撤销必须携带 `expected_version`，服务端行级锁定 + 版本号乐观控制，
  版本不匹配返回 `409 VERSION_CONFLICT`，状态迁移在单个事务内原子完成。
* 编号唯一，重复创建返回 `409 DUPLICATE_COMMAND_NO`。

### 时刻查询

`GET /api/restrictions/at?at=<UTC ISO8601>`

命中条件（全部满足）：状态为 `published`，或撤销时间晚于查询时刻的 `revoked`；
`published_at <= at`、`valid_from <= at < valid_to`。

* 以基础段与命中命令的里程边界共同切段；
* 每段取基础限速与所有覆盖命令限速的 **最低值**，达到该值的命令编号全部返回；
* 相邻段 **仅当限速值与命令编号集合均相同** 才合并。

时间必须带时区偏移（如 `2026-10-01T08:00:00Z`），服务端统一按 UTC 处理。

## 一键启动（Docker Compose）

前置：Docker 20.10+ 与 Compose v2。

```bash
docker compose up --build -d
```

启动时自动完成：拉取 PostgreSQL 16、构建应用镜像、等待数据库健康、
执行幂等建表（`schema.sql`）、启动 API。

* API 地址：`http://localhost:8080`
* 健康检查：`http://localhost:8080/healthz`
* Swagger 交互文档：`http://localhost:8080/docs`
* OpenAPI：`http://localhost:8080/openapi.json`
* 基础路径前缀：`/api`

停止与清理：

```bash
docker compose down          # 停止（保留数据卷）
docker compose down -v       # 停止并删除数据库数据卷（重置全部数据）
```

### 配置（环境变量，均可在 compose 中覆盖）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | API 监听地址 |
| `APP_PORT` | `8080` | API 监听端口（容器内） |
| `POSTGRES_USER` | `speed` | 数据库用户 |
| `POSTGRES_PASSWORD` | `speed` | 数据库密码 |
| `POSTGRES_DB` | `speed_restriction` | 数据库名 |
| `POSTGRES_PORT` | `5432` | 宿主机映射的数据库端口 |
| `DATABASE_URL` | compose 内部拼接 | PostgreSQL DSN（脱离 compose 运行时使用） |

例如改用 9090 端口：

```bash
APP_PORT=9090 docker compose up --build
# API: http://localhost:9090
```

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/line/init` | 录入总里程与基础限速段（仅一次） |
| GET | `/api/line` | 查询线路与基础段 |
| POST | `/api/commands` | 创建命令草稿 |
| GET | `/api/commands` | 列出全部命令 |
| GET | `/api/commands/{command_no}` | 查询单条命令 |
| POST | `/api/commands/{command_no}/publish` | 发布（body 带 `expected_version`） |
| POST | `/api/commands/{command_no}/revoke` | 撤销（body 带 `expected_version`） |
| GET | `/api/restrictions/at?at=...` | 查询指定 UTC 时刻的限速分段 |

错误响应统一格式：

```json
{ "error": { "code": "VERSION_CONFLICT", "message": "...",
             "details": {"expected_version": 1, "actual_version": 2} } }
```

主要错误码：`INVALID_TOTAL_KM`、`BASE_SEGMENTS_EMPTY/GAP/OVERLAP`、
`MILEAGE_OUT_OF_RANGE`、`INVALID_MILEAGE_RANGE`、`INVALID_SPEED`、
`INVALID_TIME_RANGE`、`INVALID_REQUEST`、`DUPLICATE_COMMAND_NO`、
`VERSION_CONFLICT`、`RETROACTIVE_PUBLISH`、`COMMAND_NOT_FOUND`、
`LINE_NOT_INITIALIZED`、`LINE_ALREADY_INITIALIZED`。

## 调用示例

### 1. 录入线路（总里程 100，三段连续基础限速）

```bash
curl -sS -X POST http://localhost:8080/api/line/init \
  -H 'Content-Type: application/json' \
  -d '{
    "total_km": "100",
    "base_segments": [
      {"start_km": "0",  "end_km": "40",  "speed": "160"},
      {"start_km": "40", "end_km": "80",  "speed": "120"},
      {"start_km": "80", "end_km": "100", "speed": "100"}
    ]
  }'
```

### 2. 创建并发布临时命令

```bash
# 创建（草稿 version=1；valid_from 必须是未来 UTC 时刻，否则发布时被拒）
curl -sS -X POST http://localhost:8080/api/commands \
  -H 'Content-Type: application/json' \
  -d '{
    "command_no": "T-2026-001",
    "start_km": "10", "end_km": "30", "speed": "80",
    "valid_from": "2026-10-01T09:00:00Z",
    "valid_to":   "2026-10-01T12:00:00Z"
  }'

# 发布：expected_version 必须等于当前版本；成功后版本变为 2
curl -sS -X POST http://localhost:8080/api/commands/T-2026-001/publish \
  -H 'Content-Type: application/json' -d '{"expected_version": 1}'

# 撤销（以服务端时间生效；成功后版本变为 3）
curl -sS -X POST http://localhost:8080/api/commands/T-2026-001/revoke \
  -H 'Content-Type: application/json' -d '{"expected_version": 2}'
```

### 3. 查询指定 UTC 时刻的限速分段

```bash
curl -sS "http://localhost:8080/api/restrictions/at?at=2026-10-01T10:00:00Z"
```

响应示例（基础限速 + 命中命令按边界切段、取最低、相同才合并）：

```json
{
  "at": "2026-10-01T10:00:00.000000Z",
  "total_km": "100",
  "segments": [
    {"start_km": "0",  "end_km": "10", "speed": "160", "command_nos": []},
    {"start_km": "10", "end_km": "30", "speed": "80",  "command_nos": ["T-2026-001"]},
    {"start_km": "30", "end_km": "40", "speed": "160", "command_nos": []},
    {"start_km": "40", "end_km": "80", "speed": "120", "command_nos": []},
    {"start_km": "80", "end_km": "100","speed": "100", "command_nos": []}
  ]
}
```

完整端到端脚本（含冲突、回溯、撤销、历史重放等全部场景）：

```bash
chmod +x examples/api_examples.sh
./examples/api_examples.sh
```

## 本地开发（非 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 需要一个可访问的 PostgreSQL
export DATABASE_URL="postgresql://speed:speed@localhost:5432/speed_restriction"
python -m app migrate        # 建表
python -m app serve          # 启动，默认 0.0.0.0:8080

# 纯逻辑单元测试（切段/取最低/合并，无需数据库）
python -m unittest discover -s tests -v
```

## 目录结构

```
app/
  config.py      环境配置
  db.py          psycopg3 连接池、幂等建表
  domain.py      切段/最低限速/合并纯函数（可单测）
  errors.py      统一错误模型（HTTP 状态 + 错误码）
  models.py      Pydantic 请求/响应模型（Decimal 字符串收发、UTC 时间）
  services.py    录入校验、命令生命周期事务、时刻查询
  main.py        FastAPI 路由
  __main__.py    serve / migrate CLI
schema.sql       表结构、基础段 GiST 排他约束、查询索引
tests/           领域逻辑单元测试
Dockerfile / docker-compose.yml / entrypoint.sh
examples/api_examples.sh  端到端调用示例
```
