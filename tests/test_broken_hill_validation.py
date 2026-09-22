from __future__ import annotations

import unittest

from examples.broken_hill.scripts.convert_and_compare import (
    canonical_station_key,
    summarize_differences,
)


class BrokenHillValidationTests(unittest.TestCase):
    def test_canonical_station_key_matches_source_name_variants(self) -> None:
        self.assertEqual(canonical_station_key("BH_1"), "BH_1")
        self.assertEqual(canonical_station_key("BH_1_imp"), "BH_1")
        self.assertEqual(canonical_station_key("BH-1-imp-rev"), "BH_1")
        with self.assertRaisesRegex(ValueError, "Broken Hill station"):
            canonical_station_key("station-one")

    def test_exact_station_key_mode_supports_other_surveys(self) -> None:
        self.assertEqual(
            canonical_station_key(" Station-A ", mode="exact"),
            "Station-A",
        )
        with self.assertRaisesRegex(ValueError, "station key mode"):
            canonical_station_key("Station-A", mode="unknown")

    def test_difference_summary_handles_zero_and_uses_nearest_rank_p95(self) -> None:
        summary = summarize_differences(
            ((1.0 + 0.0j, 1.0 + 0.0j), (1.0 + 0.0j, 2.0 + 0.0j), (0j, 0j))
        )
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["median_absolute_difference"], 0.0)
        self.assertEqual(summary["p95_absolute_difference"], 1.0)
        self.assertEqual(summary["maximum_absolute_difference"], 1.0)
        self.assertEqual(summary["median_symmetric_relative_difference"], 0.0)
        self.assertEqual(summary["p95_symmetric_relative_difference"], 0.5)
        self.assertEqual(summary["maximum_symmetric_relative_difference"], 0.5)


if __name__ == "__main__":
    unittest.main()
