import unittest
from decimal import Decimal

from app.service import (
    BaseCoverage,
    CommandCoverage,
    build_speed_segments,
)


class SpeedSegmentTests(unittest.TestCase):
    def test_boundaries_minimum_speed_and_command_set_merge(self):
        segments = build_speed_segments(
            Decimal("100"),
            [
                BaseCoverage(Decimal("0"), Decimal("50"), Decimal("120")),
                BaseCoverage(Decimal("50"), Decimal("100"), Decimal("100")),
            ],
            [
                CommandCoverage("A", Decimal("10"), Decimal("60"), Decimal("80")),
                CommandCoverage("B", Decimal("40"), Decimal("70"), Decimal("80")),
            ],
        )

        self.assertEqual(
            [(s.start_mileage, s.end_mileage, s.speed, s.command_numbers) for s in segments],
            [
                (Decimal("0"), Decimal("10"), Decimal("120"), ()),
                (Decimal("10"), Decimal("40"), Decimal("80"), ("A",)),
                (Decimal("40"), Decimal("60"), Decimal("80"), ("A", "B")),
                (Decimal("60"), Decimal("70"), Decimal("80"), ("B",)),
                (Decimal("70"), Decimal("100"), Decimal("100"), ()),
            ],
        )

    def test_command_end_is_exclusive_and_non_minimum_command_is_ignored(self):
        segments = build_speed_segments(
            Decimal("10"),
            [BaseCoverage(Decimal("0"), Decimal("10"), Decimal("100"))],
            [
                CommandCoverage("LOW", Decimal("0"), Decimal("5"), Decimal("80")),
                CommandCoverage("HIGH", Decimal("0"), Decimal("10"), Decimal("120")),
            ],
        )

        self.assertEqual(
            [(s.start_mileage, s.end_mileage, s.speed, s.command_numbers) for s in segments],
            [
                (Decimal("0"), Decimal("5"), Decimal("80"), ("LOW",)),
                (Decimal("5"), Decimal("10"), Decimal("100"), ()),
            ],
        )

    def test_adjacent_base_segments_merge_through_command_boundary(self):
        segments = build_speed_segments(
            Decimal("10"),
            [
                BaseCoverage(Decimal("0"), Decimal("3"), Decimal("90")),
                BaseCoverage(Decimal("3"), Decimal("10"), Decimal("90")),
            ],
            [CommandCoverage("A", Decimal("2"), Decimal("8"), Decimal("90"))],
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(
            (segments[0].start_mileage, segments[0].end_mileage, segments[0].speed),
            (Decimal("0"), Decimal("10"), Decimal("90")),
        )
        self.assertEqual(segments[0].command_numbers, ())


if __name__ == "__main__":
    unittest.main()
