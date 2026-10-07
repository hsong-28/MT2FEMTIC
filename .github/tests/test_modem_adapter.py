from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from mt2femtic.config import SourceConfig
from mt2femtic.conventions import FIELD_TO_OHM, project_station
from mt2femtic.conversion import read_observations
from tests.test_conventions import coordinate_config
from mt2femtic.model import ModemModel
from mt2femtic.modem_adapter import (
    modem_values_in_femtic_order,
    read_modem_data,
    read_modem_model,
)


FIXTURES = Path(__file__).parent / "fixtures" / "modem"


def modem_config(path: Path = FIXTURES / "survey.dat", **overrides: object) -> SourceConfig:
    values: dict[str, object] = {
        "type": "modem",
        "path": path,
        "impedance_unit": "mv_per_km_per_nt",
        "time_convention": "exp_plus_iwt",
        "allow_time_convention_override": False,
        "edi_pattern": "*.edi",
        "modem_vertical_coordinate": "elevation_m",
    }
    values.update(overrides)
    return SourceConfig(**values)  # type: ignore[arg-type]


def two_by_two_numbered_model() -> ModemModel:
    return ModemModel(
        dimensions=(2, 2, 1),
        north_widths_m=(1000.0, 1000.0),
        east_widths_m=(1000.0, 1000.0),
        depth_widths_m=(500.0,),
        resistivity_ohm_m=(1.0, 2.0, 3.0, 4.0),
        origin_m=(0.0, 0.0, 0.0),
        rotation_deg=0.0,
        representation="LINEAR",
    )


class ModemAdapterTests(unittest.TestCase):
    def test_missing_vertical_declaration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "modem_vertical_coordinate"):
            read_modem_data(FIXTURES / "survey.dat", modem_config(modem_vertical_coordinate=None))

    def test_declared_depth_matches_conversion_without_datum_shift(self) -> None:
        path = Path(__file__).parents[2] / "examples/minimal/input/modem/complete.dat"
        config = modem_config(path, modem_vertical_coordinate="depth_m")
        source = read_modem_data(path, config).stations[0]
        projected, _ = project_station(source, coordinate_config(vertical_datum_elevation_m=500))
        converted = read_observations(path, "modem").stations[0]
        self.assertIsNone(source.elevation_m)
        self.assertEqual(projected.surface_depth_km, -0.1)
        self.assertEqual(projected.surface_depth_km, converted.surface_depth_km)
        self.assertEqual(projected.samples, source.samples)

    def test_declared_legacy_elevation_keeps_datum_formula(self) -> None:
        config = modem_config(modem_vertical_coordinate="elevation_m")
        source = read_modem_data(FIXTURES / "survey.dat", config).stations[0]
        projected, _ = project_station(source, coordinate_config(vertical_datum_elevation_m=1000))
        self.assertEqual(source.elevation_m, 750)
        self.assertEqual(projected.surface_depth_km, 0.25)

    def test_units_conflict_is_rejected_even_with_time_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "units.*conflict"):
            read_modem_data(FIXTURES / "survey.dat", modem_config(
                impedance_unit="ohm", allow_time_convention_override=True))

    def test_nonzero_modem_header_orientation_is_rejected(self) -> None:
        fixture = Path(__file__).parents[2] / "examples/minimal/input/modem/complete.dat"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rotated.dat"
            path.write_text(fixture.read_text().replace("> 0\n> 0 0", "> 30\n> 0 0"))
            with self.assertRaisesRegex(ValueError, "orientation"):
                read_modem_data(path, modem_config(path))

    def test_full_header_missing_sign_preserves_explicit_time_override(self) -> None:
        fixture = Path(__file__).parents[2] / "examples/minimal/input/modem/complete.dat"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing-sign.dat"
            path.write_text(fixture.read_text().replace(r"> exp(+i\omega t)", ">"))
            with self.assertRaisesRegex(ValueError, "Missing ModEM time convention"):
                read_modem_data(path, modem_config(path))
            survey = read_modem_data(path, modem_config(path, allow_time_convention_override=True))
            self.assertTrue(survey.metadata["time_convention_override"])

    def test_data_uses_north_east_and_conjugates(self) -> None:
        survey = read_modem_data(FIXTURES / "survey.dat", modem_config())
        station = survey.stations[0]
        self.assertEqual((station.north_m, station.east_m), (1000.0, 2000.0))
        sample = station.samples[0]
        self.assertAlmostEqual(sample.values["ZXY"].imag, -2.0 * FIELD_TO_OHM)
        self.assertAlmostEqual(sample.standard_errors["ZXY"], 0.5 * FIELD_TO_OHM)
        self.assertEqual(sample.values["TX"], 0.1 - 0.2j)
        self.assertEqual(sample.standard_errors["TX"], 0.03)

    def test_station_and_frequency_order_is_stable(self) -> None:
        survey = read_modem_data(FIXTURES / "survey.dat", modem_config())
        self.assertEqual([station.name for station in survey.stations], ["S01", "S02"])
        self.assertEqual([sample.frequency_hz for sample in survey.stations[0].samples], [1.0, 0.1])

    def test_header_config_conflict_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts with configured"):
            read_modem_data(
                FIXTURES / "survey.dat",
                modem_config(time_convention="exp_minus_iwt"),
            )

    def test_missing_convention_requires_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing_convention.dat"
            text = (FIXTURES / "survey.dat").read_text(encoding="ascii")
            path.write_text(text.replace("> exp(+i\\omega t)\n", ""), encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Missing ModEM time convention"):
                read_modem_data(path, modem_config(path=path))
            survey = read_modem_data(
                path,
                modem_config(path=path, allow_time_convention_override=True),
            )
            self.assertTrue(survey.metadata["time_convention_override"])

    def test_loge_model_is_decoded(self) -> None:
        model = read_modem_model(FIXTURES / "model_loge.rho")
        self.assertEqual(model.dimensions, (2, 1, 1))
        self.assertAlmostEqual(model.resistivity_ohm_m[0], 100.0)
        self.assertAlmostEqual(model.resistivity_ohm_m[1], 200.0)

    def test_north_rows_are_reversed_for_femtic(self) -> None:
        model = two_by_two_numbered_model()
        self.assertEqual(modem_values_in_femtic_order(model), (2.0, 1.0, 4.0, 3.0))

    def test_nonpositive_model_width_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.rho"
            text = (FIXTURES / "model_loge.rho").read_text(encoding="ascii")
            path.write_text(text.replace("1000 2000", "0 2000"), encoding="ascii")
            with self.assertRaisesRegex(ValueError, "north widths must contain finite positive values"):
                read_modem_model(path)


if __name__ == "__main__":
    unittest.main()
