"""核心切段合并逻辑的单元测试（不依赖数据库）。"""

from __future__ import annotations

import unittest
from decimal import Decimal as D

from app.domain import Restriction as R, compute_segments


def cmd(start, end, speed, no):
    return R(D(start), D(end), D(speed), no)


def base(start, end, speed):
    return R(D(start), D(end), D(speed), None)


class ComputeSegmentsTests(unittest.TestCase):
    def test_no_commands_returns_base_segments(self):
        segs = compute_segments(
            D("10"),
            [base("0", "5", "160"), base("5", "10", "120")],
            [],
        )
        self.assertEqual(
            [(s.start_km, s.end_km, s.speed, s.command_nos) for s in segs],
            [
                (D("0"), D("5"), D("160"), tuple()),
                (D("5"), D("10"), D("120"), tuple()),
            ],
        )

    def test_single_command_splits_at_its_boundaries(self):
        segs = compute_segments(
            D("10"),
            [base("0", "10", "160")],
            [cmd("3", "7", "80", "T001")],
        )
        self.assertEqual(
            [(s.start_km, s.end_km, s.speed, s.command_nos) for s in segs],
            [
                (D("0"), D("3"), D("160"), tuple()),
                (D("3"), D("7"), D("80"), ("T001",)),
                (D("7"), D("10"), D("160"), tuple()),
            ],
        )

    def test_overlapping_commands_take_minimum_speed(self):
        segs = compute_segments(
            D("10"),
            [base("0", "10", "200")],
            [cmd("0", "8", "120", "A"), cmd("4", "10", "60", "B")],
        )
        self.assertEqual(
            [(s.start_km, s.end_km, s.speed, s.command_nos) for s in segs],
            [
                (D("0"), D("4"), D("120"), ("A",)),
                (D("4"), D("10"), D("60"), ("B",)),
            ],
        )

    def test_equal_minimum_keeps_all_command_numbers(self):
        segs = compute_segments(
            D("10"),
            [base("0", "10", "200")],
            [cmd("2", "6", "80", "A"), cmd("4", "8", "80", "B")],
        )
        # 4-6 段两条命令并列最低，编号全部保留且排序
        middle = next(s for s in segs if s.start_km == D("4"))
        self.assertEqual(middle.speed, D("80"))
        self.assertEqual(middle.command_nos, ("A", "B"))
        self.assertEqual(middle.end_km, D("6"))

    def test_merge_only_when_speed_and_command_set_match(self):
        # 两侧同为 80，但命令编号集合不同（A / A,B / A），不得合并
        segs = compute_segments(
            D("10"),
            [base("0", "10", "160")],
            [cmd("0", "10", "80", "A"), cmd("3", "7", "80", "B")],
        )
        self.assertEqual(
            [(s.start_km, s.end_km, s.speed, s.command_nos) for s in segs],
            [
                (D("0"), D("3"), D("80"), ("A",)),
                (D("3"), D("7"), D("80"), ("A", "B")),
                (D("7"), D("10"), D("80"), ("A",)),
            ],
        )

    def test_merge_adjacent_identical_segments(self):
        # 命令 80 跨过基础段边界 5：限速/编号一致，相邻段合并为整段
        segs = compute_segments(
            D("10"),
            [base("0", "5", "160"), base("5", "10", "120")],
            [cmd("0", "10", "80", "A")],
        )
        self.assertEqual(len(segs), 1)
        self.assertEqual(
            (segs[0].start_km, segs[0].end_km, segs[0].speed,
             segs[0].command_nos),
            (D("0"), D("10"), D("80"), ("A",)),
        )

    def test_command_higher_than_base_does_not_change_speed(self):
        segs = compute_segments(
            D("10"),
            [base("0", "10", "100")],
            [cmd("2", "8", "150", "A")],
        )
        # 命令不低于基础限速，全部段保持基础限速且无命令编号
        self.assertEqual(len(segs), 1)
        self.assertEqual(
            (segs[0].start_km, segs[0].end_km, segs[0].speed,
             segs[0].command_nos),
            (D("0"), D("10"), D("100"), tuple()),
        )

    def test_commands_are_clipped_to_line_range(self):
        segs = compute_segments(
            D("10"),
            [base("0", "10", "160")],
            [cmd("-2", "3", "60", "L"), cmd("8", "20", "60", "R")],
        )
        self.assertEqual(
            [(s.start_km, s.end_km, s.command_nos) for s in segs],
            [
                (D("0"), D("3"), ("L",)),
                (D("3"), D("8"), tuple()),
                (D("8"), D("10"), ("R",)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
