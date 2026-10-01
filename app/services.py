"""业务服务层：录入校验、命令生命周期（乐观锁事务）、时刻查询。

所有时间判定以数据库事务内的 ``now()`` 为权威服务端时间，
保证发布/撤销与历史查询的可重现性。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import errors as pg_errors

from app.db import get_pool
from app.domain import Restriction, compute_segments
from app.errors import APIError, bad_request, conflict, not_found
from app.models import (
    CommandOut,
    CreateCommandIn,
    InitializeLineIn,
    LineOut,
    RestrictionAtOut,
    RestrictionSegmentOut,
)

DRAFT = "draft"
PUBLISHED = "published"
REVOKED = "revoked"


# ---------------------------------------------------------------------------
# 线路与基础限速段
# ---------------------------------------------------------------------------

def initialize_line(payload: InitializeLineIn) -> LineOut:
    """录入线路总里程与覆盖全线、互不重叠的基础限速段；录入后不可改。"""
    total_km = payload.total_km
    if total_km <= 0:
        raise bad_request("INVALID_TOTAL_KM", "线路总里程必须为正数")

    if not payload.base_segments:
        raise bad_request(
            "BASE_SEGMENTS_EMPTY", "基础限速段不能为空，必须覆盖全线"
        )

    segments = sorted(
        payload.base_segments, key=lambda s: (s.start_km, s.end_km)
    )

    cursor_km = Decimal(0)
    for seg in segments:
        if seg.speed <= 0:
            raise bad_request(
                "INVALID_SPEED",
                f"基础段 [{seg.start_km}, {seg.end_km}) 限速必须为正数",
            )
        if seg.start_km < 0 or seg.end_km < 0:
            raise bad_request(
                "MILEAGE_OUT_OF_RANGE", "基础段里程不得为负数"
            )
        if seg.end_km > total_km or seg.start_km > total_km:
            raise bad_request(
                "MILEAGE_OUT_OF_RANGE",
                f"基础段 [{seg.start_km}, {seg.end_km}) 超出线路总里程 {total_km}",
            )
        if seg.start_km >= seg.end_km:
            raise bad_request(
                "INVALID_MILEAGE_RANGE",
                f"基础段 [{seg.start_km}, {seg.end_km}) 为空或逆序（左闭右开）",
            )
        if seg.start_km != cursor_km:
            if seg.start_km < cursor_km:
                raise bad_request(
                    "BASE_SEGMENTS_OVERLAP",
                    f"基础段在 {seg.start_km} 处与前序段重叠",
                )
            raise bad_request(
                "BASE_SEGMENTS_GAP",
                f"基础段在 [{cursor_km}, {seg.start_km}) 处存在缺口",
            )
        cursor_km = seg.end_km

    if cursor_km != total_km:
        raise bad_request(
            "BASE_SEGMENTS_GAP",
            f"基础段必须覆盖全线，末端为 {cursor_km}，线路总里程为 {total_km}",
        )

    pool = get_pool()
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM line_config WHERE id = 1 FOR UPDATE")
                if cur.fetchone() is not None:
                    raise conflict(
                        "LINE_ALREADY_INITIALIZED",
                        "线路与基础限速段已录入，录入后不可修改",
                    )
                cur.execute(
                    "INSERT INTO line_config (id, total_km) VALUES (1, %s)",
                    (total_km,),
                )
                cur.executemany(
                    """
                    INSERT INTO base_segments (line_id, start_km, end_km, speed)
                    VALUES (1, %s, %s, %s)
                    """,
                    [
                        (s.start_km, s.end_km, s.speed)
                        for s in segments
                    ],
                )
            conn.commit()
    except pg_errors.UniqueViolation:
        # 并发初始化竞态（FOR UPDATE 对不存在的行不互斥）兜底
        raise conflict(
            "LINE_ALREADY_INITIALIZED",
            "线路与基础限速段已录入，录入后不可修改",
        )
    except pg_errors.ExclusionViolation:
        # GiST 排他约束兜底（正常应由应用层校验拦截）
        raise bad_request(
            "BASE_SEGMENTS_OVERLAP", "基础限速段存在重叠"
        )

    line = get_line()
    if line is None:  # 不可能到达，保证类型完整
        raise APIError(500, "INTERNAL", "线路初始化失败")
    return line


def get_line() -> LineOut | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT total_km FROM line_config WHERE id = 1")
            line = cur.fetchone()
            if line is None:
                return None
            cur.execute(
                """
                SELECT start_km, end_km, speed
                FROM base_segments
                WHERE line_id = 1
                ORDER BY start_km
                """
            )
            rows = cur.fetchall()
    return LineOut(
        total_km=line["total_km"],
        initialized=True,
        base_segments=[
            {
                "start_km": r["start_km"],
                "end_km": r["end_km"],
                "speed": r["speed"],
            }
            for r in rows
        ],
    )


def _require_line(cur) -> Decimal:
    cur.execute("SELECT total_km FROM line_config WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        raise conflict(
            "LINE_NOT_INITIALIZED",
            "请先录入线路总里程与基础限速段",
        )
    return row["total_km"]


# ---------------------------------------------------------------------------
# 临时命令：创建草稿、发布、撤销（乐观版本 + 原子事务）
# ---------------------------------------------------------------------------

def create_command(payload: CreateCommandIn) -> CommandOut:
    if payload.command_no != payload.command_no.strip() or not payload.command_no:
        raise bad_request("INVALID_COMMAND_NO", "命令编号不能为空")

    if payload.speed <= 0:
        raise bad_request("INVALID_SPEED", "限速值必须为正数")

    if payload.start_km < 0 or payload.end_km < 0:
        raise bad_request("MILEAGE_OUT_OF_RANGE", "里程不得为负数")

    if payload.start_km >= payload.end_km:
        raise bad_request(
            "INVALID_MILEAGE_RANGE",
            "里程范围为空或逆序（左闭右开，须 start_km < end_km）",
        )

    if payload.valid_from >= payload.valid_to:
        raise bad_request(
            "INVALID_TIME_RANGE",
            "UTC 生效时段为空或逆序（左闭右开，须 valid_from < valid_to）",
        )

    pool = get_pool()
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                total_km = _require_line(cur)
                if payload.end_km > total_km:
                    raise bad_request(
                        "MILEAGE_OUT_OF_RANGE",
                        f"命令里程超出线路总里程 {total_km}",
                    )
                cur.execute(
                    """
                    INSERT INTO commands (
                        command_no, start_km, end_km, speed,
                        valid_from, valid_to, status, version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                    RETURNING *
                    """,
                    (
                        payload.command_no,
                        payload.start_km,
                        payload.end_km,
                        payload.speed,
                        payload.valid_from,
                        payload.valid_to,
                        DRAFT,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
    except pg_errors.UniqueViolation:
        raise conflict(
            "DUPLICATE_COMMAND_NO",
            f"命令编号 {payload.command_no} 已存在",
        )

    return CommandOut.model_validate(row)


def _fetch_command(cur, command_no: str) -> dict[str, Any]:
    cur.execute(
        "SELECT * FROM commands WHERE command_no = %s",
        (command_no,),
    )
    row = cur.fetchone()
    if row is None:
        raise not_found(
            "COMMAND_NOT_FOUND", f"命令 {command_no} 不存在"
        )
    return row


def publish_command(command_no: str, expected_version: int) -> CommandOut:
    """发布命令；不得回溯生效（valid_from 不得早于服务端当前时刻）。"""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now() AS now")
            server_now: datetime = cur.fetchone()["now"]

            cur.execute(
                "SELECT * FROM commands WHERE command_no = %s FOR UPDATE",
                (command_no,),
            )
            row = cur.fetchone()
            if row is None:
                raise not_found(
                    "COMMAND_NOT_FOUND", f"命令 {command_no} 不存在"
                )

            if row["version"] != expected_version:
                raise conflict(
                    "VERSION_CONFLICT",
                    f"版本冲突：期望 {expected_version}，实际 {row['version']}",
                    {
                        "expected_version": expected_version,
                        "actual_version": row["version"],
                    },
                )

            status = row["status"]
            if status == PUBLISHED:
                raise conflict(
                    "COMMAND_ALREADY_PUBLISHED",
                    f"命令 {command_no} 已发布",
                    {"actual_version": row["version"]},
                )
            if status == REVOKED:
                raise conflict(
                    "COMMAND_REVOKED",
                    f"命令 {command_no} 已撤销，不能再次发布",
                    {"actual_version": row["version"]},
                )

            if row["valid_from"] <= server_now:
                raise conflict(
                    "RETROACTIVE_PUBLISH",
                    "发布不得回溯生效：valid_from 必须晚于服务端当前 UTC 时间",
                    {"server_time": server_now.isoformat()},
                )

            cur.execute(
                """
                UPDATE commands
                   SET status = %s,
                       published_at = %s,
                       version = version + 1,
                       updated_at = %s
                 WHERE command_no = %s
                 RETURNING *
                """,
                (PUBLISHED, server_now, server_now, command_no),
            )
            row = cur.fetchone()
        conn.commit()
    return CommandOut.model_validate(row)


def revoke_command(command_no: str, expected_version: int) -> CommandOut:
    """撤销命令；以服务端当前时刻为撤销生效时间（历史时刻仍可重现）。"""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now() AS now")
            server_now: datetime = cur.fetchone()["now"]

            cur.execute(
                "SELECT * FROM commands WHERE command_no = %s FOR UPDATE",
                (command_no,),
            )
            row = cur.fetchone()
            if row is None:
                raise not_found(
                    "COMMAND_NOT_FOUND", f"命令 {command_no} 不存在"
                )

            if row["version"] != expected_version:
                raise conflict(
                    "VERSION_CONFLICT",
                    f"版本冲突：期望 {expected_version}，实际 {row['version']}",
                    {
                        "expected_version": expected_version,
                        "actual_version": row["version"],
                    },
                )

            status = row["status"]
            if status == DRAFT:
                raise conflict(
                    "COMMAND_NOT_PUBLISHED",
                    f"命令 {command_no} 尚未发布，不能撤销",
                    {"actual_version": row["version"]},
                )
            if status == REVOKED:
                raise conflict(
                    "COMMAND_ALREADY_REVOKED",
                    f"命令 {command_no} 已撤销",
                    {"actual_version": row["version"]},
                )

            cur.execute(
                """
                UPDATE commands
                   SET status = %s,
                       revoked_at = %s,
                       version = version + 1,
                       updated_at = %s
                 WHERE command_no = %s
                 RETURNING *
                """,
                (REVOKED, server_now, server_now, command_no),
            )
            row = cur.fetchone()
        conn.commit()
    return CommandOut.model_validate(row)


def get_command(command_no: str) -> CommandOut:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            row = _fetch_command(cur, command_no)
    return CommandOut.model_validate(row)


def list_commands() -> list[CommandOut]:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM commands ORDER BY command_no")
            rows = cur.fetchall()
    return [CommandOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# 时刻查询
# ---------------------------------------------------------------------------

def restrictions_at(at: datetime) -> RestrictionAtOut:
    """查询指定 UTC 时刻全线限速分段。

    参与计算的命令：已发布（published），或撤销时间晚于查询时刻的已撤销命令；
    且查询时刻落在命令生效时段 ``[valid_from, valid_to)`` 内，
    同时发布时间不晚于查询时刻。撤销前的历史时刻因此可稳定重现。
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            total_km = _require_line(cur)
            cur.execute(
                """
                SELECT start_km, end_km, speed
                FROM base_segments
                WHERE line_id = 1
                ORDER BY start_km
                """
            )
            base_rows = cur.fetchall()
            cur.execute(
                """
                SELECT command_no, start_km, end_km, speed
                FROM commands
                WHERE (
                        status = 'published'
                        OR (status = 'revoked' AND revoked_at > %s)
                      )
                  AND published_at <= %s
                  AND valid_from <= %s
                  AND valid_to > %s
                ORDER BY command_no
                """,
                (at, at, at, at),
            )
            cmd_rows = cur.fetchall()

    base = [
        Restriction(
            Decimal(str(r["start_km"])),
            Decimal(str(r["end_km"])),
            Decimal(str(r["speed"])),
            None,
        )
        for r in base_rows
    ]
    commands = [
        Restriction(
            Decimal(str(r["start_km"])),
            Decimal(str(r["end_km"])),
            Decimal(str(r["speed"])),
            r["command_no"],
        )
        for r in cmd_rows
    ]

    segments = compute_segments(Decimal(str(total_km)), base, commands)
    return RestrictionAtOut(
        at=at,
        total_km=total_km,
        segments=[
            RestrictionSegmentOut(
                start_km=s.start_km,
                end_km=s.end_km,
                speed=s.speed,
                command_nos=list(s.command_nos),
            )
            for s in segments
        ],
    )
