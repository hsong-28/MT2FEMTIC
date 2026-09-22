from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mt2femtic.config import CoordinateConfig
from mt2femtic.model import Station, Survey
from mt2femtic.single_survey import read_edi_stage
from mt2femtic.single_survey.projection import project_sites_stage, project_survey
from tests.test_single_survey_case import valid_payload


ROOT = Path(__file__).parents[1]
EDI_FIXTURE = ROOT / "tests/fixtures/edi/plus_iwt.edi"


class SingleSurveyProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        edi = self.root / "0-EDI"
        edi.mkdir()
        shutil.copy2(EDI_FIXTURE, edi / "station.edi")
        (self.root / "survey.json").write_text(
            json.dumps(valid_payload(), indent=2) + "\n", encoding="utf-8"
        )
        read_edi_stage(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stage_writes_projected_coordinates_and_manifest(self) -> None:
        report = project_sites_stage(self.root)
        output = self.root / "2-Projection"
        self.assertEqual(report["station_count"], 1)
        self.assertLessEqual(report["maximum_round_trip_error_m"], 0.01)
        self.assertEqual(report["resolved_coordinates"]["model_axis_azimuth_deg"], 0.0)
        with (output / "stations_projected.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            row = next(csv.DictReader(stream))
        self.assertAlmostEqual(float(row["model_x_km"]), float(row["north_offset_m"]) / 1000.0)
        self.assertAlmostEqual(float(row["model_y_km"]), float(row["east_offset_m"]) / 1000.0)
        self.assertAlmostEqual(float(row["surface_depth_km"]), -float(row["elevation_m"]) / 1000.0)
        xyz = (output / "site_xyz.dat").read_text(encoding="ascii").split()
        self.assertAlmostEqual(float(xyz[0]), float(row["model_y_km"]))
        self.assertAlmostEqual(float(xyz[1]), float(row["model_x_km"]))
        self.assertEqual(xyz[-1], "S01")
        manifest_text = (output / "stage-manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), manifest_text)

    def test_stage_refuses_overwrite(self) -> None:
        project_sites_stage(self.root)
        with self.assertRaisesRegex(FileExistsError, "2-Projection"):
            project_sites_stage(self.root)

    def test_stage_rejects_changed_previous_output(self) -> None:
        path = self.root / "1-DataOri/site_lonlat.dat"
        path.write_text(path.read_text(encoding="ascii") + "changed\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            project_sites_stage(self.root)
        self.assertFalse((self.root / "2-Projection").exists())

    def test_stage_rejects_incomplete_previous_hash_set(self) -> None:
        path = self.root / "1-DataOri/stage-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["output_hashes"].pop("site_lonlat.dat")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "output hash set mismatch"):
            project_sites_stage(self.root)

    def test_stage_rejects_changed_source(self) -> None:
        path = self.root / "0-EDI/station.edi"
        path.write_text(path.read_text(encoding="ascii") + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            project_sites_stage(self.root)
        self.assertFalse((self.root / "2-Projection").exists())

    def test_nonzero_azimuth_uses_existing_rotation_contract(self) -> None:
        payload = json.loads((self.root / "survey.json").read_text(encoding="utf-8"))
        payload["coordinates"]["model_axis_azimuth_deg"] = 90.0
        (self.root / "survey.json").write_text(json.dumps(payload), encoding="utf-8")
        project_sites_stage(self.root)
        with (self.root / "2-Projection/stations_projected.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            row = next(csv.DictReader(stream))
        self.assertAlmostEqual(float(row["model_x_km"]), float(row["east_offset_m"]) / 1000.0)
        self.assertAlmostEqual(float(row["model_y_km"]), -float(row["north_offset_m"]) / 1000.0)

    def test_duplicate_projected_locations_fail(self) -> None:
        station = Station(1, "A", None, None, 0.0, 0.0, 0.0, None, None, None, ())
        survey = Survey((station, Station(**{**station.__dict__, "station_id": 2, "name": "B"})), "ohm", "exp_minus_iwt", "edi")
        config = CoordinateConfig("EPSG:4326", "EPSG:32633", 0.0, 0.0, 0.0, 0.0, 0.01, "north_east")
        with self.assertRaisesRegex(ValueError, "Duplicate projected station location"):
            project_survey(survey, config)

    def test_script_prints_json_report(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/02_project_sites.py"), "--root", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(json.loads(result.stdout)["station_count"], 1)


if __name__ == "__main__":
    unittest.main()
