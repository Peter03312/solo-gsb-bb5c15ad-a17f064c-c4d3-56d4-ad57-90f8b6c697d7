"""核心领域逻辑：按里程边界切段、重叠取最低限速、相邻段合并。

里程区间一律左闭右开 ``[start_km, end_km)``，数值使用 Decimal 精确比较。
本模块不依赖数据库，便于单元测试与结果可重现性验证。
"""

from __future__ import annotations

from collections import namedtuple
from decimal import Decimal

# 一条参与切段的限速来源：命令编号为 None 表示基础限速。
Restriction = namedtuple(
    "Restriction", ["start_km", "end_km", "speed", "command_no"]
)
# 输出段：里程范围、生效限速值、达到该限速的命令编号集合（已排序）。
Segment = namedtuple(
    "Segment", ["start_km", "end_km", "speed", "command_nos"]
)


def _overlap(
    start_km: Decimal,
    end_km: Decimal,
    seg_start: Decimal,
    seg_end: Decimal,
) -> tuple[Decimal, Decimal] | None:
    lo = max(start_km, seg_start)
    hi = min(end_km, seg_end)
    return (lo, hi) if lo < hi else None


def compute_segments(
    total_km: Decimal,
    base_segments: list[Restriction],
    active_commands: list[Restriction],
) -> list[Segment]:
    """计算 ``[0, total_km)`` 全线在某一时刻的限速分段。

    规则：
    * 以基础段里程边界与命令里程边界共同切段；
    * 每个切段上，基础限速与所有覆盖该段的命令限速取最低值；
    * 达到最低值的命令编号全部保留（并列最低），基础限速不带编号；
    * 相邻切段仅在限速值与命令编号集合均相同时合并。
    """

    # 收集全部切段边界点（裁剪到全线范围）。
    points: set[Decimal] = {Decimal(0), Decimal(total_km)}
    for rs in (*base_segments, *active_commands):
        if rs.end_km > Decimal(0) and rs.start_km < total_km:
            points.add(max(Decimal(0), rs.start_km))
            points.add(min(total_km, rs.end_km))

    boundaries = sorted(points)

    raw: list[Segment] = []
    for seg_start, seg_end in zip(boundaries, boundaries[1:]):
        winning_speed: Decimal | None = None
        winning_commands: list[str] = []

        # 基础段中恰好一条覆盖该切段（录入时已保证连续全覆盖不重叠）。
        for rs in base_segments:
            ov = _overlap(rs.start_km, rs.end_km, seg_start, seg_end)
            if ov is not None:
                winning_speed = rs.speed
                break

        # 临时命令：覆盖该段的全部限速，取最低；并列最低全部保留。
        for cmd in active_commands:
            ov = _overlap(cmd.start_km, cmd.end_km, seg_start, seg_end)
            if ov is None:
                continue
            if winning_speed is None or cmd.speed < winning_speed:
                winning_speed = cmd.speed
                winning_commands = [cmd.command_no]
            elif cmd.speed == winning_speed:
                winning_commands.append(cmd.command_no)

        if winning_speed is None:  # 基础段理论上全覆盖，防御性处理。
            continue
        raw.append(
            Segment(
                seg_start,
                seg_end,
                winning_speed,
                tuple(sorted(winning_commands)),
            )
        )

    # 相邻段合并：限速与编号集合均相同才合并。
    merged: list[Segment] = []
    for seg in raw:
        if (
            merged
            and merged[-1].end_km == seg.start_km
            and merged[-1].speed == seg.speed
            and merged[-1].command_nos == seg.command_nos
        ):
            merged[-1] = Segment(
                merged[-1].start_km,
                seg.end_km,
                seg.speed,
                seg.command_nos,
            )
        else:
            merged.append(seg)
    return merged
