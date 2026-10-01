#!/usr/bin/env bash
# 等待 PostgreSQL 就绪 -> 初始化数据库结构 -> 启动 HTTP 服务
set -euo pipefail

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"

echo "等待 PostgreSQL ${POSTGRES_HOST}:${POSTGRES_PORT} ..."
for i in $(seq 1 60); do
    if pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER:-speed}" >/dev/null 2>&1; then
        echo "PostgreSQL 已就绪"
        break
    fi
    sleep 1
    if [ "$i" -eq 60 ]; then
        echo "PostgreSQL 在限定时间内未就绪" >&2
        exit 1
    fi
done

# 幂等建表（CREATE TABLE / EXTENSION / 索引均为 IF NOT EXISTS）
python -m app migrate

exec python -m app serve
