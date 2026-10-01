#!/usr/bin/env bash
# 铁路区间临时限速编排服务 —— 端到端调用示例（bash + curl + jq 可选）
#
# 用法：
#   ./examples/api_examples.sh            # BASE_URL 默认 http://localhost:8080
#   BASE_URL=http://1.2.3.4:8080 ./examples/api_examples.sh
#
# 依赖：bash 4+、curl、GNU coreutils date（Linux 自带；macOS 用 gdate）。
# 输出中的命令响应均含 version，发布/撤销必须携带 expected_version。
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
API="${BASE_URL}/api"
DATE_BIN="${DATE_BIN:-date}"

# 相对当前 UTC 生成 ISO 8601（秒级精度，带 Z 后缀），例如 iso -3600 / iso 7200
iso() {
    "${DATE_BIN}" -u -d "@$(( $("${DATE_BIN}" +%s) + $1 ))" +%Y-%m-%dT%H:%M:%SZ
}

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
req() { # METHOD PATH [JSON_BODY]
    local method="$1" path="$2" body="${3:-}"
    if [ -n "$body" ]; then
        curl -sS -X "$method" -H 'Content-Type: application/json' \
             -d "$body" "${API}${path}"
    else
        curl -sS -X "$method" "${API}${path}"
    fi
    printf '\n'
}

# 时间窗（全部 UTC，左闭右开）
FROM_1H=$(iso 3600)      # 1 小时后生效 —— 保证发布不回溯
TO_3H=$(iso 10800)       # 3 小时后失效
FROM_2H=$(iso 7200)      # 2 小时后生效
TO_5H=$(iso 18000)       # 5 小时后失效
NOW=$(iso 0)
PAST=$(iso -3600)

say "1) 健康检查"
curl -sS "${BASE_URL}/healthz"; echo

say "2) 录入线路（总里程 100km，基础段连续全覆盖、互不重叠）"
req POST /line/init '{
  "total_km": "100",
  "base_segments": [
    {"start_km": "0",   "end_km": "40", "speed": "160"},
    {"start_km": "40",  "end_km": "80", "speed": "120"},
    {"start_km": "80",  "end_km": "100","speed": "100"}
  ]
}'

say "3) 再次录入 -> 409 LINE_ALREADY_INITIALIZED（基础段录入后不可改）"
req POST /line/init '{"total_km":"100","base_segments":[
  {"start_km":"0","end_km":"100","speed":"200"}]}'

say "4) 创建临时命令 T-2026-001（草稿，不参与查询）：K10~K30 限速 80"
req POST /commands "{
  \"command_no\": \"T-2026-001\",
  \"start_km\": \"10\", \"end_km\": \"30\", \"speed\": \"80\",
  \"valid_from\": \"${FROM_1H}\", \"valid_to\": \"${TO_3H}\"
}"

say "5) 重复编号 -> 409 DUPLICATE_COMMAND_NO"
req POST /commands "{
  \"command_no\": \"T-2026-001\",
  \"start_km\": \"1\", \"end_km\": \"2\", \"speed\": \"80\",
  \"valid_from\": \"${FROM_1H}\", \"valid_to\": \"${TO_3H}\"
}"

say "6) 创建第二条命令 T-2026-002：K20~K50 限速 60（与 001 在 K20~K30 重叠）"
req POST /commands "{
  \"command_no\": \"T-2026-002\",
  \"start_km\": \"20\", \"end_km\": \"50\", \"speed\": \"60\",
  \"valid_from\": \"${FROM_2H}\", \"valid_to\": \"${TO_5H}\"
}"

say "7) 用错误版本发布 -> 409 VERSION_CONFLICT"
req POST /commands/T-2026-001/publish '{"expected_version": 99}'

say "8) 正确版本发布 T-2026-001（草稿 version=1，发布成功后 version=2）"
req POST /commands/T-2026-001/publish '{"expected_version": 1}'

say "9) 重复发布 -> 409 COMMAND_ALREADY_PUBLISHED"
req POST /commands/T-2026-001/publish '{"expected_version": 2}'

say "10) 回溯发布：T-2026-002 的 valid_from 改到过去需新建命令演示"
req POST /commands "{
  \"command_no\": \"T-2026-RETRO\",
  \"start_km\": \"50\", \"end_km\": \"60\", \"speed\": \"40\",
  \"valid_from\": \"${PAST}\", \"valid_to\": \"${TO_3H}\"
}"
req POST /commands/T-2026-RETRO/publish '{"expected_version": 1}'

say "11) 校验失败示例：非正限速 / 空范围 / 越界里程"
req POST /commands "{
  \"command_no\": \"T-BAD-1\", \"start_km\": \"10\", \"end_km\": \"20\",
  \"speed\": \"0\", \"valid_from\": \"${FROM_1H}\", \"valid_to\": \"${TO_3H}\"}"
req POST /commands "{
  \"command_no\": \"T-BAD-2\", \"start_km\": \"90\", \"end_km\": \"120\",
  \"speed\": \"80\", \"valid_from\": \"${FROM_1H}\", \"valid_to\": \"${TO_3H}\"}"
req POST /commands "{
  \"command_no\": \"T-BAD-3\", \"start_km\": \"30\", \"end_km\": \"30\",
  \"speed\": \"80\", \"valid_from\": \"${FROM_1H}\", \"valid_to\": \"${TO_3H}\"}"

say "12) 发布 T-2026-002"
req POST /commands/T-2026-002/publish '{"expected_version": 1}'

say "13) 撤销前查询未来 90 分钟：仅 T-2026-001 生效（002 未到 valid_from）"
FUT90=$(iso 5400)
req GET "/restrictions/at?at=${FUT90}"

say "14) 撤销 T-2026-001（expected_version=2；撤销按服务端时间立即生效）"
req POST /commands/T-2026-001/revoke '{"expected_version": 2}'

say "15) 同一未来时刻再查：001 已被撤销且撤销时间早于该时刻 -> 仅剩基础限速"
req GET "/restrictions/at?at=${FUT90}"

say "16) 历史可重现（一）：查询 1 小时前，两命令均未生效，等同基础限速"
req GET "/restrictions/at?at=${PAST}"

say "17) 历史可重现（二）：查询未来 3 小时，T-2026-002 生效（001 已失效）"
FUT3H=$(iso 10800)
req GET "/restrictions/at?at=${FUT3H}"
echo "--- 立即重放同一时刻，结果必须与上面逐字节一致（已发布数据未变）---"
req GET "/restrictions/at?at=${FUT3H}"

say "18) 非法查询时刻（无时区 / 非法格式）-> 400"
req GET "/restrictions/at?at=2026-10-01T00:00:00"
req GET "/restrictions/at?at=not-a-time"

echo
echo "完成。交互文档: ${BASE_URL}/docs"
