from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt2femtic.config import (
    ObservationRefinementConfig,
    SelectionConfig,
    SourceConfig,
)
from mt2femtic.edi_adapter import read_edi_directory
from mt2femtic.femtic_writer import write_femtic_inputs
from mt2femtic.manifest import sha256_file
from mt2femtic.selection import prepare_selected_survey


ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "femticpy"
EDI = FIXTURE_ROOT / "synthetic_01.edi"


class FemticPyCompatibilityTests(unittest.TestCase):
    def _survey(self):
        source = SourceConfig(
            type="edi",
            path=FIXTURE_ROOT,
            impedance_unit="mv_per_km_per_nt",
            time_convention="exp_plus_iwt",
            allow_time_convention_override=True,
            edi_pattern="synthetic_01.edi",
        )
        return read_edi_directory(source)

    def test_pinned_fixture_identity_and_provenance(self) -> None:
        self.assertEqual(
            sha256_file(EDI),
            "8b931877321e1c3e191656ece808a8125056d3f3c07eaa379cc3547f8c6c68c0",
        )
        provenance = (FIXTURE_ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
        self.assertIn("1b0fe316c01714d06a1c3be2bef5fa47daa67f0c", provenance)
        self.assertIn("projects/synthetic/input_data/edi_files/01.edi", provenance)
        self.assertIn("MIT", provenance)

    def test_station_metadata_unit_scale_and_conjugation(self) -> None:
        survey = self._survey()
        station = survey.stations[0]
        self.assertEqual(station.name, "01")
        self.assertEqual(station.latitude_deg, -90.0)
        self.assertEqual(station.longitude_deg, 0.0)
        self.assertEqual(station.elevation_m, 0.0)
        sample = station.samples[0]
        scale = 1000.0 * 4.0 * math.pi * 1.0e-7
        self.assertAlmostEqual(sample.values["ZXY"].real, 73.85204 * scale)
        self.assertAlmostEqual(sample.values["ZXY"].imag, -193.2458 * scale)
        self.assertEqual(survey.time_convention, "exp_minus_iwt")
        self.assertEqual(survey.metadata["time_convention_overrides"], ["01"])

    def test_error_floors_and_frequency_order(self) -> None:
        survey = self._survey()
        config = SelectionConfig(
            periods_s=(0.01, 0.1, 1.0, 10.0, 100.0, 1000.0),
            relative_tolerance=1.0e-9,
            impedance_error_floor_fraction=0.01,
            vtf_error_floor_absolute=0.01,
            max_vtf_period_s=10000.0,
            frequency_policy="exact",
        )
        selected = prepare_selected_survey(survey, config)
        samples = selected.stations[0].samples
        self.assertEqual(
            [sample.frequency_hz for sample in samples],
            [100.0, 10.0, 1.0, 0.1, 0.01, 0.001],
        )
        expected = 0.01 * math.sqrt(
            abs(samples[0].values["ZXY"]) * abs(samples[0].values["ZYX"])
        )
        self.assertAlmostEqual(samples[0].standard_errors["ZXY"], expected)
        self.assertAlmostEqual(samples[0].standard_errors["ZYX"], expected)
        self.assertEqual(samples[0].standard_errors["TX"], 0.01)
        self.assertEqual(samples[0].standard_errors["TY"], 0.01)

    def test_canonical_observe_structure_is_documented(self) -> None:
        survey = self._survey()
        station = replace(
            survey.stations[0], model_x_km=0.0, model_y_km=0.0
        )
        survey = replace(survey, stations=(station,))
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_femtic_inputs(
                survey,
                Path(temporary),
                ObservationRefinementConfig(radius_km=1.0, level=1, weight=0.5),
            )
            lines = paths["observe"].read_text(encoding="ascii").splitlines()
        self.assertEqual(lines[0], "MT 1")
        self.assertEqual(lines[1], "1 1001 0 0 0")
        self.assertEqual(lines[2], "6")
        vtf_index = lines.index("VTF 1")
        self.assertEqual(lines[vtf_index + 1], "1001 1001 1 0 0")
        self.assertEqual(lines[-1], "END")

    def test_compatibility_scope_is_explicit(self) -> None:
        report = (
            ROOT / "docs" / "femticpy-compatibility.md"
        ).read_text(encoding="utf-8")
        for heading in (
            "Agreements",
            "Intentional policy differences",
            "File-contract differences",
            "Excluded scope",
        ):
            self.assertIn(heading, report)
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("FEMTICPy", notices)
        self.assertIn("MIT", notices)


if __name__ == "__main__":
    unittest.main()
