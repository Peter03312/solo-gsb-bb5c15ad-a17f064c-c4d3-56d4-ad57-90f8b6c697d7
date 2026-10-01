"""FastAPI 入口：铁路区间临时限速编排服务 HTTP API。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import services
from app.config import settings
from app.db import close_pool, init_pool, init_schema
from app.errors import APIError
from app.models import (
    CommandOut,
    CreateCommandIn,
    InitializeLineIn,
    LineOut,
    PublishCommandIn,
    RestrictionAtOut,
    RevokeCommandIn,
    UtcDatetime,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    init_schema()
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title="铁路区间临时限速编排服务",
    version="1.0.0",
    lifespan=lifespan,
)

P = settings.api_prefix.rstrip("/")


@app.exception_handler(APIError)
async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "请求参数不合法",
                "details": {"issues": _simplify_issues(exc.errors())},
            }
        },
    )


def _simplify_issues(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    simplified: list[dict[str, Any]] = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        msg = err.get("msg", "参数错误")
        # 透传自定义校验器的中文原因（type=value_error 时 msg 即 ValueError 文本）
        if "Value error, " in msg:
            msg = msg.split("Value error, ", 1)[1]
        simplified.append({"field": loc, "message": msg})
    return simplified


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "service": "railway-temporary-speed-restriction",
        "docs": "/docs",
        "api": P,
    }


# --- 线路与基础限速段（录入后不可改）----------------------------------------

@app.post(
    f"{P}/line/init",
    response_model=LineOut,
    status_code=201,
    tags=["line"],
    summary="录入线路总里程与基础限速段（仅允许一次）",
)
def initialize_line(payload: InitializeLineIn) -> LineOut:
    return services.initialize_line(payload)


@app.get(
    f"{P}/line",
    response_model=LineOut,
    tags=["line"],
    summary="查询线路总里程与基础限速段",
)
def get_line() -> LineOut:
    line = services.get_line()
    if line is None:
        from app.errors import conflict

        raise conflict(
            "LINE_NOT_INITIALIZED",
            "请先录入线路总里程与基础限速段",
        )
    return line


# --- 临时命令 --------------------------------------------------------------

@app.post(
    f"{P}/commands",
    response_model=CommandOut,
    status_code=201,
    tags=["commands"],
    summary="创建临时限速命令（草稿，不参与查询）",
)
def create_command(payload: CreateCommandIn) -> CommandOut:
    return services.create_command(payload)


@app.get(
    f"{P}/commands",
    response_model=list[CommandOut],
    tags=["commands"],
    summary="列出全部命令",
)
def list_commands() -> list[CommandOut]:
    return services.list_commands()


@app.get(
    f"{P}/commands/{{command_no}}",
    response_model=CommandOut,
    tags=["commands"],
    summary="查询单条命令（含状态与版本）",
)
def get_command(command_no: str) -> CommandOut:
    return services.get_command(command_no)


@app.post(
    f"{P}/commands/{{command_no}}/publish",
    response_model=CommandOut,
    tags=["commands"],
    summary="发布命令（校验预期版本；不得回溯生效）",
)
def publish_command(command_no: str, payload: PublishCommandIn) -> CommandOut:
    return services.publish_command(command_no, payload.expected_version)


@app.post(
    f"{P}/commands/{{command_no}}/revoke",
    response_model=CommandOut,
    tags=["commands"],
    summary="撤销命令（校验预期版本；按服务端时间生效）",
)
def revoke_command(command_no: str, payload: RevokeCommandIn) -> CommandOut:
    return services.revoke_command(command_no, payload.expected_version)


# --- 时刻查询 --------------------------------------------------------------

@app.get(
    f"{P}/restrictions/at",
    response_model=RestrictionAtOut,
    tags=["restrictions"],
    summary="查询指定 UTC 时刻的全线限速分段",
)
def restrictions_at(at: UtcDatetime) -> RestrictionAtOut:
    return services.restrictions_at(at)
