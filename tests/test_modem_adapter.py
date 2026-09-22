from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from mt2femtic.config import SourceConfig
from mt2femtic.conventions import FIELD_TO_OHM
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
