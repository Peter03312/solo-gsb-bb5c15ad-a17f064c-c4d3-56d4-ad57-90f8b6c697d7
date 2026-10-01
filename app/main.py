"""FastAPI HTTP endpoints for temporary railway speed restrictions."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import service
from app.database import BaseSegment, Command, Line, engine, initialize_database
from app.schemas import (
    CancelCommandIn,
    CommandIn,
    CommandOut,
    LineIn,
    LineOut,
    PublishCommandIn,
    QueryAtIn,
    QueryResponse,
    SegmentOut,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="铁路区间临时限速编排服务",
    version="1.0.0",
    lifespan=lifespan,
)


def get_session():
    with Session(engine) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]


def get_line_or_409(session: Session, *, for_update: bool = False) -> Line:
    statement = select(Line).options(selectinload(Line.base_segments))
    if for_update:
        statement = statement.with_for_update()
    line = session.scalar(statement)
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="线路尚未初始化；请先录入总里程和覆盖全线的基础限速段",
        )
    return line


def get_command_or_404(
    session: Session, number: str, *, for_update: bool = False
) -> Command:
    statement = select(Command).where(Command.number == number)
    if for_update:
        statement = statement.with_for_update()
    command = session.scalar(statement)
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="命令不存在")
    return command


def ensure_version(command: Command, expected_version: int) -> None:
    if command.version != expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"版本冲突：预期版本 {expected_version}，"
                f"当前版本 {command.version}"
            ),
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/line",
    response_model=LineOut,
    status_code=status.HTTP_201_CREATED,
    tags=["线路"],
)
def create_line(payload: LineIn, session: DbSession) -> Line:
    try:
        ordered = payload.ordered_segments()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    if session.scalar(select(Line.id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="线路和基础限速段已经录入；基础段录入后不可修改",
        )

    line = Line(id=1, total_mileage=payload.total_mileage)
    line.base_segments = [
        BaseSegment(
            line_id=1,
            start_mileage=segment.start_mileage,
            end_mileage=segment.end_mileage,
            speed=segment.speed,
        )
        for segment in ordered
    ]
    session.add(line)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="线路已经存在，或基础限速段发生重叠",
        )

    session.refresh(line)
    return get_line_or_409(session)


@app.get("/line", response_model=LineOut, tags=["线路"])
def read_line(session: DbSession) -> Line:
    return get_line_or_409(session)


@app.post(
    "/commands",
    response_model=CommandOut,
    status_code=status.HTTP_201_CREATED,
    tags=["临时命令"],
)
def create_command(payload: CommandIn, session: DbSession) -> Command:
    line = get_line_or_409(session)
    try:
        payload.validate_range(Decimal(str(line.total_mileage)))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    command = Command(
        number=payload.number,
        line_id=line.id,
        line_total_mileage=line.total_mileage,
        start_mileage=payload.start_mileage,
        end_mileage=payload.end_mileage,
        speed=payload.speed,
        effective_start=payload.effective_start,
        effective_end=payload.effective_end,
        status="draft",
        version=0,
    )
    session.add(command)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="命令编号重复"
        )
    session.refresh(command)
    return command


@app.get("/commands/{number}", response_model=CommandOut, tags=["临时命令"])
def read_command(number: str, session: DbSession) -> Command:
    return get_command_or_404(session, number)


@app.get("/commands", response_model=list[CommandOut], tags=["临时命令"])
def list_commands(session: DbSession) -> list[Command]:
    return list(session.scalars(select(Command).order_by(Command.number)))


@app.post(
    "/commands/{number}/publish",
    response_model=CommandOut,
    tags=["临时命令"],
)
def publish_command(
    number: str, payload: PublishCommandIn, session: DbSession
) -> Command:
    """Atomically check the expected version and publish.

    Publication time is the PostgreSQL transaction time. A command may become
    effective at or after that server time, so publication cannot apply
    retroactively.
    """
    command = get_command_or_404(session, number, for_update=True)
    ensure_version(command, payload.expected_version)
    if command.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"仅草稿命令可以发布；当前状态为 {command.status}",
        )

    now = session.execute(select(func.current_timestamp())).scalar_one()
    if command.effective_start < now:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="发布不得回溯生效：生效起点不能早于服务端当前时间",
        )

    command.status = "published"
    command.published_at = now
    command.version += 1
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="发布时发生版本或状态冲突"
        )
    return get_command_or_404(session, number)


@app.post(
    "/commands/{number}/cancel",
    response_model=CommandOut,
    tags=["临时命令"],
)
def cancel_command(
    number: str, payload: CancelCommandIn, session: DbSession
) -> Command:
    """Atomically check version and make cancellation effective at server time."""
    command = get_command_or_404(session, number, for_update=True)
    ensure_version(command, payload.expected_version)
    if command.status != "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"仅已发布命令可以撤销；当前状态为 {command.status}",
        )

    now = session.execute(select(func.current_timestamp())).scalar_one()
    command.status = "cancelled"
    command.cancelled_at = now
    command.version += 1
    session.commit()
    return get_command_or_404(session, number)


def _coerce_query_time(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="查询时刻必须带时区；请使用 UTC ISO-8601 格式",
        )
    return at.astimezone(timezone.utc)


@app.get("/query", response_model=QueryResponse, tags=["查询"])
def query_at_get(
    session: DbSession,
    at: Annotated[
        datetime,
        Query(
            description="UTC 查询时刻，ISO-8601，例如 2027-01-01T01:30:00Z",
        ),
    ],
) -> QueryResponse:
    return query_speed_segments(session, _coerce_query_time(at))


@app.post("/query", response_model=QueryResponse, tags=["查询"])
def query_at_post(payload: QueryAtIn, session: DbSession) -> QueryResponse:
    return query_speed_segments(session, payload.at)


def query_speed_segments(session: Session, at: datetime) -> QueryResponse:
    line = get_line_or_409(session)
    base_rows = sorted(line.base_segments, key=lambda item: item.start_mileage)

    # 左闭右开：effective_start <= at < effective_end。
    # 撤销按服务端时间生效；查询撤销前时刻时历史结果仍可重现。
    command_rows = list(
        session.scalars(
            select(Command)
            .where(
                Command.status.in_(("published", "cancelled")),
                Command.effective_start <= at,
                Command.effective_end > at,
                or_(
                    Command.status == "published",
                    Command.cancelled_at.is_(None),
                    Command.cancelled_at > at,
                ),
            )
            .order_by(Command.number)
        )
    )

    calculated = service.build_speed_segments(
        Decimal(str(line.total_mileage)),
        [
            service.BaseCoverage(
                Decimal(str(item.start_mileage)),
                Decimal(str(item.end_mileage)),
                Decimal(str(item.speed)),
            )
            for item in base_rows
        ],
        [
            service.CommandCoverage(
                item.number,
                Decimal(str(item.start_mileage)),
                Decimal(str(item.end_mileage)),
                Decimal(str(item.speed)),
            )
            for item in command_rows
        ],
    )

    return QueryResponse(
        at=at,
        total_mileage=Decimal(str(line.total_mileage)),
        segments=[
            SegmentOut(
                start_mileage=item.start_mileage,
                end_mileage=item.end_mileage,
                speed=item.speed,
                command_numbers=list(item.command_numbers),
            )
            for item in calculated
        ],
    )
