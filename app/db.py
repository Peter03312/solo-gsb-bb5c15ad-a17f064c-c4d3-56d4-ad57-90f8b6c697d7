"""PostgreSQL 连接池与结构初始化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg import Cursor
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

_pool: ConnectionPool | None = None


class DictCursor(Cursor):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["row_factory"] = dict_row
        super().__init__(*args, **kwargs)


def init_pool(min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """创建并预热连接池（幂等）。"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"cursor_factory": DictCursor},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        return init_pool()
    return _pool


def init_schema() -> None:
    """幂等执行 schema.sql（CREATE TABLE / INDEX 均为 IF NOT EXISTS）。"""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
