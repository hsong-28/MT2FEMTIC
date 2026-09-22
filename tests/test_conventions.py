from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from mt2femtic.config import CoordinateConfig
from mt2femtic.conventions import (
    FIELD_TO_OHM,
    elevation_to_depth_km,
    impedance_scale_to_ohm,
    normalize_complex,
    project_station,
    rotate_to_model_km,
    write_convention_audit,
)
from mt2femtic.model import Station


def coordinate_config(**overrides: object) -> CoordinateConfig:
    values: dict[str, object] = {
        "geographic_crs": "EPSG:4326",
        "projected_crs": "EPSG:32632",
        "origin_easting_m": 500000.0,
        "origin_northing_m": 5538630.702867474,
        "model_axis_azimuth_deg": 0.0,
        "vertical_datum_elevation_m": 1000.0,
        "round_trip_tolerance_m": 0.01,
        "modem_axis_convention": "north_east",
    }
    values.update(overrides)
    return CoordinateConfig(**values)  # type: ignore[arg-type]


def station(**overrides: object) -> Station:
    values: dict[str, object] = {
        "station_id": 1,
        "name": "S01",
        "longitude_deg": 9.0,
        "latitude_deg": 50.0,
        "elevation_m": 750.0,
        "north_m": None,
        "east_m": None,
        "model_x_km": None,
        "model_y_km": None,
        "surface_depth_km": None,
        "samples": (),
    }
    values.update(overrides)
    return Station(**values)  # type: ignore[arg-type]


class ConventionTests(unittest.TestCase):
    def test_plus_iwt_is_conjugated_once(self) -> None:
        self.assertEqual(normalize_complex(3.0 + 4.0j, "exp_plus_iwt"), 3.0 - 4.0j)

    def test_minus_iwt_is_unchanged(self) -> None:
        self.assertEqual(normalize_complex(3.0 - 4.0j, "exp_minus_iwt"), 3.0 - 4.0j)

    def test_unknown_time_convention_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported time convention"):
            normalize_complex(1.0 + 2.0j, "unknown")

    def test_field_unit_factor(self) -> None:
        self.assertAlmostEqual(
            impedance_scale_to_ohm("mv_per_km_per_nt"),
            1000.0 * 4.0e-7 * math.pi,
        )
        self.assertAlmostEqual(FIELD_TO_OHM, 0.0012566370614359172)

    def test_ohm_unit_is_unchanged(self) -> None:
        self.assertEqual(impedance_scale_to_ohm("ohm"), 1.0)

    def test_zero_azimuth_maps_north_to_x_and_east_to_y(self) -> None:
        self.assertEqual(rotate_to_model_km(2000.0, 3000.0, 0.0), (2.0, 3.0))

    def test_ninety_degree_azimuth_rotates_axes(self) -> None:
        x_km, y_km = rotate_to_model_km(2000.0, 3000.0, 90.0)
        self.assertAlmostEqual(x_km, 3.0)
        self.assertAlmostEqual(y_km, -2.0)

    def test_depth_is_positive_down(self) -> None:
        self.assertEqual(elevation_to_depth_km(750.0, 1250.0), 0.5)

    def test_geographic_station_is_projected_and_round_tripped(self) -> None:
        projected, round_trip_error_m = project_station(station(), coordinate_config())
        self.assertAlmostEqual(projected.model_x_km or 0.0, 0.0, places=5)
        self.assertAlmostEqual(projected.model_y_km or 0.0, 0.0, places=5)
        self.assertEqual(projected.surface_depth_km, 0.25)
        self.assertLess(round_trip_error_m, 0.01)

    def test_declared_local_coordinates_are_authoritative(self) -> None:
        source = station(longitude_deg=None, latitude_deg=None, north_m=2000.0, east_m=3000.0)
        projected, round_trip_error_m = project_station(source, coordinate_config())
        self.assertEqual((projected.model_x_km, projected.model_y_km), (2.0, 3.0))
        self.assertEqual(round_trip_error_m, 0.0)

    def test_audit_is_written_as_sorted_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.json"
            write_convention_audit(path, {"target": "exp_minus_iwt", "scale": 1.0})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["target"],
                "exp_minus_iwt",
            )


if __name__ == "__main__":
    unittest.main()
