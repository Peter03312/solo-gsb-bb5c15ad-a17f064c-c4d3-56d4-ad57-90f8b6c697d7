"""Pure speed-coverage calculation used by query API and tests."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence


@dataclass(frozen=True)
class BaseCoverage:
    start_mileage: Decimal
    end_mileage: Decimal
    speed: Decimal


@dataclass(frozen=True)
class CommandCoverage:
    number: str
    start_mileage: Decimal
    end_mileage: Decimal
    speed: Decimal


@dataclass(frozen=True)
class ResultSegment:
    start_mileage: Decimal
    end_mileage: Decimal
    speed: Decimal
    command_numbers: tuple[str, ...]


def build_speed_segments(
    total_mileage: Decimal,
    base_segments: Sequence[BaseCoverage],
    active_commands: Iterable[CommandCoverage],
) -> list[ResultSegment]:
    """Return minimum-speed coverage on [0, total_mileage).

    All inputs and outputs use Decimal. Commands are compared against the base
    segment containing each interval. Adjacent pieces are merged only when
    speed and the full contributing command-number set are identical.
    """
    boundaries: set[Decimal] = {Decimal(0), total_mileage}
    for segment in base_segments:
        boundaries.add(segment.start_mileage)
        boundaries.add(segment.end_mileage)
    for command in active_commands:
        boundaries.add(command.start_mileage)
        boundaries.add(command.end_mileage)

    ordered_boundaries = sorted(boundaries)
    if not ordered_boundaries or ordered_boundaries[0] != 0:
        raise ValueError("基础限速段必须从里程 0 开始")
    if ordered_boundaries[-1] != total_mileage:
        raise ValueError("基础限速段未覆盖完整线路")

    pieces: list[ResultSegment] = []
    base_index = 0

    for start, end in zip(ordered_boundaries, ordered_boundaries[1:]):
        if start >= end:
            continue

        while base_index + 1 < len(base_segments) and (
            base_segments[base_index].end_mileage <= start
        ):
            base_index += 1
        base = base_segments[base_index]
        if not (base.start_mileage <= start < end <= base.end_mileage):
            raise ValueError("基础限速段存在缺口或未覆盖指定区间")

        minimum_speed = base.speed
        contributors: list[CommandCoverage] = []
        for command in active_commands:
            # 左闭右开：命令终点边界处不再生效。
            if command.start_mileage <= start and end <= command.end_mileage:
                if command.speed < minimum_speed:
                    minimum_speed = command.speed
                    contributors = [command]
                elif command.speed == minimum_speed:
                    contributors.append(command)

        if minimum_speed == base.speed:
            numbers = tuple()
        else:
            numbers = tuple(sorted(command.number for command in contributors))
        pieces.append(
            ResultSegment(start, end, minimum_speed, numbers)
        )

    merged: list[ResultSegment] = []
    for piece in pieces:
        if (
            merged
            and merged[-1].end_mileage == piece.start_mileage
            and merged[-1].speed == piece.speed
            and merged[-1].command_numbers == piece.command_numbers
        ):
            previous = merged.pop()
            piece = ResultSegment(
                previous.start_mileage,
                piece.end_mileage,
                piece.speed,
                piece.command_numbers,
            )
        merged.append(piece)

    return merged
