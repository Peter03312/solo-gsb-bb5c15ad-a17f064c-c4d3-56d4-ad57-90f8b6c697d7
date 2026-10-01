"""命令行入口：

* ``python -m app serve``   启动 HTTP 服务
* ``python -m app migrate`` 仅初始化/刷新数据库结构
"""

from __future__ import annotations

import sys

import uvicorn

from app.config import settings
from app.db import close_pool, init_pool, init_schema


def migrate() -> None:
    init_pool()
    init_schema()
    close_pool()
    print("数据库结构初始化完成")


def serve() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "migrate":
        migrate()
    elif cmd == "serve":
        serve()
    else:
        print(f"未知命令: {cmd}（支持 serve / migrate）", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
