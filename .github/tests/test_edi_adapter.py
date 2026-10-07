from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt2femtic.config import SourceConfig
from mt2femtic.conventions import FIELD_TO_OHM
from mt2femtic.edi_adapter import read_edi_directory, read_edi_file


FIXTURES = Path(__file__).parent / "fixtures" / "edi"


def edi_config(path: Path = FIXTURES, **overrides: object) -> SourceConfig:
    values: dict[str, object] = {
        "type": "edi",
        "path": path,
        "impedance_unit": "mv_per_km_per_nt",
        "time_convention": "exp_plus_iwt",
        "allow_time_convention_override": False,
        "edi_pattern": "*.edi",
    }
    values.update(overrides)
    return SourceConfig(**values)  # type: ignore[arg-type]


class EDIAdapterTests(unittest.TestCase):
    def test_processing_note_plus_iwt_is_detected(self) -> None:
        station = read_edi_file(FIXTURES / "plus_iwt.edi", edi_config())
        sample = station.samples[0]
        self.assertAlmostEqual(sample.values["ZXY"].imag, -2.0 * FIELD_TO_OHM)
        self.assertAlmostEqual(sample.standard_errors["ZXY"], 2.0 * FIELD_TO_OHM)
        self.assertEqual(sample.values["TX"], 0.1 - 0.2j)

    def test_standard_minus_iwt_is_preserved(self) -> None:
        config = edi_config(time_convention="exp_minus_iwt")
        station = read_edi_file(FIXTURES / "minus_iwt.edi", config)
        self.assertAlmostEqual(station.samples[0].values["ZXY"].imag, -2.0 * FIELD_TO_OHM)

    def test_missing_convention_requires_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "Missing EDI time convention"):
            read_edi_file(FIXTURES / "missing_convention.edi", edi_config())

    def test_explicit_override_is_recorded_by_directory_reader(self) -> None:
        config = edi_config(
            path=FIXTURES,
            edi_pattern="missing_convention.edi",
            allow_time_convention_override=True,
        )
        survey = read_edi_directory(config)
        self.assertEqual(survey.metadata["time_convention_overrides"], ["S03"])

    def test_metadata_config_conflict_fails_without_override(self) -> None:
        config = edi_config(time_convention="exp_minus_iwt")
        with self.assertRaisesRegex(ValueError, "conflicts with configured"):
            read_edi_file(FIXTURES / "plus_iwt.edi", config)

    def test_directory_assigns_stable_ids(self) -> None:
        config = edi_config(
            edi_pattern="*.edi",
            allow_time_convention_override=True,
        )
        survey = read_edi_directory(config)
        self.assertEqual([station.station_id for station in survey.stations], [1, 2, 3])
        self.assertEqual(survey.impedance_unit, "ohm")
        self.assertEqual(survey.time_convention, "exp_minus_iwt")

    def test_conflicting_station_names_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = (FIXTURES / "plus_iwt.edi").read_text(encoding="ascii")
            (root / "a.edi").write_text(text, encoding="ascii")
            (root / "b.edi").write_text(text, encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Duplicate station name: S01"):
                read_edi_directory(replace(edi_config(), path=root))

    def test_null_dataid_uses_info_station_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public.edi"
            text = (FIXTURES / "plus_iwt.edi").read_text(encoding="ascii")
            text = text.replace("DATAID=S01", "DATAID=None")
            text = text.replace(
                ">FREQ //1",
                ">INFO\nSTATION NAME: BH_1\n>FREQ //1",
            )
            path.write_text(text, encoding="ascii")
            station = read_edi_file(path, edi_config())
            self.assertEqual(station.name, "BH_1")

    def test_empty_marker_deactivates_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.edi"
            text = (FIXTURES / "plus_iwt.edi").read_text(encoding="ascii")
            text = text.replace("DATAID=S01", "DATAID=S01\nEMPTY=1.0E32")
            text = text.replace(">ZXYR //1\n1.0", ">ZXYR //1\n1.0E32")
            path.write_text(text, encoding="ascii")
            sample = read_edi_file(path, edi_config()).samples[0]
            self.assertNotIn("ZXY", sample.active_components)
            self.assertNotIn("ZXY", sample.values)
            self.assertNotIn("ZXY", sample.standard_errors)
            self.assertIn("ZYX", sample.active_components)

    def test_partial_component_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "partial.edi"
            text = (FIXTURES / "plus_iwt.edi").read_text(encoding="ascii")
            text = text.replace("NFREQ=1", "NFREQ=2").replace(">FREQ //1\n1.0", ">FREQ //2\n1.0 0.1")
            path.write_text(text, encoding="ascii")
            with self.assertRaisesRegex(ValueError, "ZXYR contains 1 values; expected 2"):
                read_edi_file(path, edi_config())


if __name__ == "__main__":
    unittest.main()
