from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mt2femtic.conventions import FIELD_TO_OHM
from mt2femtic.single_survey.edi import read_edi_stage
from tests.test_single_survey_case import valid_payload


ROOT = Path(__file__).parents[1]
EDI_FIXTURE = ROOT / "tests/fixtures/edi/plus_iwt.edi"


class SingleSurveyEdiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        edi = self.root / "0-EDI"
        edi.mkdir()
        shutil.copy2(EDI_FIXTURE, edi / "station.edi")
        (self.root / "survey.json").write_text(
            json.dumps(valid_payload(), indent=2) + "\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stage_writes_normalized_original_files(self) -> None:
        before = sha256((self.root / "0-EDI/station.edi").read_bytes()).hexdigest()
        report = read_edi_stage(self.root)
        output = self.root / "1-DataOri"
        self.assertEqual(report["station_count"], 1)
        self.assertEqual(report["sample_count"], 1)
        self.assertEqual(report["output_time_convention"], "exp_minus_iwt")
        self.assertEqual(report["source_impedance_unit"], "mv_per_km_per_nt")
        self.assertEqual(report["source_time_convention"], "exp_plus_iwt")
        for name in (
            "site_lonlat.dat",
            "imp_ori.dat",
            "tip_ori.dat",
            "frefile.dat",
            "check_unit.dat",
            "stage-manifest.json",
        ):
            self.assertTrue((output / name).is_file(), name)
        lines = (output / "imp_ori.dat").read_text(encoding="ascii").splitlines()
        self.assertEqual(lines[0].split(), ["MT", "1"])
        values = [float(value) for value in lines[3].split()]
        self.assertAlmostEqual(values[4], -2.0 * FIELD_TO_OHM)
        self.assertEqual((output / "frefile.dat").read_text(encoding="ascii"), "1\n")
        after = sha256((self.root / "0-EDI/station.edi").read_bytes()).hexdigest()
        self.assertEqual(after, before)
        manifest_text = (output / "stage-manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(list(manifest["input_hashes"]), ["0-EDI/station.edi"])
        self.assertNotIn(str(self.root), manifest_text)

    def test_stage_refuses_to_overwrite(self) -> None:
        read_edi_stage(self.root)
        with self.assertRaisesRegex(FileExistsError, "1-DataOri"):
            read_edi_stage(self.root)


if __name__ == "__main__":
    unittest.main()
