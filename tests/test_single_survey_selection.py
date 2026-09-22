from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mt2femtic.single_survey import read_edi_stage, project_sites_stage
from mt2femtic.single_survey.selection import select_data_stage
from tests.test_single_survey_case import valid_payload


ROOT = Path(__file__).parents[1]
EDI_FIXTURE = ROOT / "tests/fixtures/edi/plus_iwt.edi"


class SingleSurveySelectionTests(unittest.TestCase):
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
        project_sites_stage(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stage_selects_and_writes_valid_femtic_files(self) -> None:
        report = select_data_stage(self.root)
        output = self.root / "3-DataSelected4Inv"
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["selection"]["frequency_policy"], "exact")
        self.assertEqual(report["femtic_validation"]["mt_sample_count"], 1)
        self.assertEqual(report["femtic_validation"]["vtf_sample_count"], 1)
        for name in (
            "observe.dat", "obs_site.dat", "distortion_iter0.dat",
            "stage-manifest.json",
        ):
            self.assertTrue((output / name).is_file(), name)
        rows = (output / "observe.dat").read_text(encoding="ascii").splitlines()
        mt = [float(value) for value in rows[3].split()]
        self.assertEqual(mt[9:11], [-1.0, -1.0])
        manifest_text = (output / "stage-manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), manifest_text)

    def test_stage_refuses_overwrite(self) -> None:
        select_data_stage(self.root)
        with self.assertRaisesRegex(FileExistsError, "3-DataSelected4Inv"):
            select_data_stage(self.root)

    def test_stage_rejects_changed_projection_output(self) -> None:
        path = self.root / "2-Projection/stations_projected.csv"
        path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "output hash mismatch"):
            select_data_stage(self.root)
        self.assertFalse((self.root / "3-DataSelected4Inv").exists())

    def test_stage_rejects_incomplete_projection_hash_set(self) -> None:
        path = self.root / "2-Projection/stage-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["output_hashes"].pop("site_xyz.dat")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "output hash set mismatch"):
            select_data_stage(self.root)

    def test_stage_rejects_changed_edi_source(self) -> None:
        path = self.root / "0-EDI/station.edi"
        path.write_text(path.read_text(encoding="ascii") + "\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            select_data_stage(self.root)
        self.assertFalse((self.root / "3-DataSelected4Inv").exists())

    def test_stage_rejects_unavailable_period(self) -> None:
        root = self.root.parent / f"{self.root.name}-unavailable"
        root.mkdir()
        try:
            shutil.copytree(self.root / "0-EDI", root / "0-EDI")
            payload = valid_payload()
            payload["selection"]["periods_s"] = [1000.0]
            (root / "survey.json").write_text(json.dumps(payload), encoding="utf-8")
            read_edi_stage(root)
            project_sites_stage(root)
            with self.assertRaisesRegex(ValueError, "Requested period 1000 s is unavailable"):
                select_data_stage(root)
            self.assertFalse((root / "3-DataSelected4Inv").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_script_prints_json_report(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/03_select_data.py"), "--root", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(json.loads(result.stdout)["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
