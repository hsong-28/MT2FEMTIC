from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mt2femtic.config import CoordinateConfig, TopographyConfig
from mt2femtic.model import Station
from mt2femtic.qa_plot import plot_station_topography
from mt2femtic.topography import prepare_topography, validate_topography_coverage


FIXTURE = Path(__file__).parent / "fixtures" / "topography_north_east_elevation.dat"


def coordinate_config() -> CoordinateConfig:
    return CoordinateConfig(
        geographic_crs="EPSG:4326",
        projected_crs="EPSG:32632",
        origin_easting_m=500000.0,
        origin_northing_m=5500000.0,
        model_axis_azimuth_deg=0.0,
        vertical_datum_elevation_m=1000.0,
        round_trip_tolerance_m=0.01,
        modem_axis_convention="north_east",
    )


def topo_config(enabled: bool = True) -> TopographyConfig:
    return TopographyConfig(
        enabled=enabled,
        path=FIXTURE if enabled else None,
        columns="north_east_elevation_m" if enabled else None,
    )


def station(x_km: float, y_km: float) -> Station:
    return Station(
        station_id=1,
        name="S01",
        longitude_deg=None,
        latitude_deg=None,
        elevation_m=750.0,
        north_m=x_km * 1000.0,
        east_m=y_km * 1000.0,
        model_x_km=x_km,
        model_y_km=y_km,
        surface_depth_km=0.25,
        samples=(),
    )


class TopographyTests(unittest.TestCase):
    def test_output_is_north_east_depth_km(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "topography.dat"
            summary = prepare_topography(topo_config(), coordinate_config(), output)
            first = [float(value) for value in output.read_text().splitlines()[0].split()]
            self.assertEqual(first, [1.0, 2.0, 0.5])
            self.assertEqual(summary.point_count, 4)

    def test_insufficient_coverage_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = prepare_topography(
                topo_config(),
                coordinate_config(),
                Path(temporary) / "topography.dat",
            )
            with self.assertRaisesRegex(ValueError, "Topography does not cover station S01"):
                validate_topography_coverage(
                    summary,
                    (station(5.0, 3.0),),
                    (1.0, 3.0, 2.0, 4.0),
                )

    def test_flat_earth_writes_no_topography_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "topography.dat"
            summary = prepare_topography(topo_config(False), coordinate_config(), output)
            self.assertFalse(summary.enabled)
            self.assertFalse(output.exists())

    def test_qa_plot_is_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = prepare_topography(
                topo_config(),
                coordinate_config(),
                root / "topography.dat",
            )
            image = root / "station_topography_check.png"
            plot_station_topography((station(2.0, 3.0),), summary, image, "unit", "EPSG:32632", 0.0)
            self.assertGreater(image.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
