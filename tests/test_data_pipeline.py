from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mt2femtic.manifest import sha256_file

from tests.test_data_config import valid_data_config_payload


FIXTURES = Path(__file__).parent / "fixtures"
EDI_FIXTURE = FIXTURES / "edi" / "plus_iwt.edi"
MODEM_FIXTURE = FIXTURES / "modem" / "survey.dat"


class DataPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "data-output"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_config(self, source_type: str = "edi", mutate=None) -> Path:
        payload = valid_data_config_payload()
        source = payload["source"]
        coordinates = payload["coordinates"]
        selection = payload["selection"]
        assert isinstance(source, dict)
        assert isinstance(coordinates, dict)
        assert isinstance(selection, dict)
        coordinates.update(
            {
                "projected_crs": "EPSG:32632",
                "origin_easting_m": 500000.0,
                "origin_northing_m": 5538630.702867474,
            }
        )
        selection["periods_s"] = [1.0]
        if source_type == "edi":
            edi_root = self.root / "edi"
            edi_root.mkdir(exist_ok=True)
            (edi_root / "S01.edi").write_bytes(EDI_FIXTURE.read_bytes())
            source["path"] = str(edi_root)
        else:
            source.update(
                {
                    "type": "modem",
                    "path": str(MODEM_FIXTURE),
                    "impedance_unit": "mv_per_km_per_nt",
                    "time_convention": "exp_plus_iwt",
                }
            )
        if mutate is not None:
            mutate(payload)
        path = self.root / f"{source_type}-data.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _data(self, config_path: Path):
        from mt2femtic.data_pipeline import data

        return data(config_path, self.output)

    def test_edi_data_package_is_complete_and_has_no_mesh_products(self) -> None:
        manifest = self._data(self._write_config())
        self.assertEqual(manifest.stages["femtic_input"].status, "passed")
        required = {
            "original/source_inventory.json",
            "projection/stations_projected.csv",
            "inversion_input/observe.dat",
            "inversion_input/obs_site.dat",
            "inversion_input/distortion_iter0.dat",
            "qa/station_topography.png",
            "qa/period_coverage.png",
            "coordinate_convention_audit.json",
            "mt2femtic_data.log",
            "mt2femtic_data_manifest.json",
        }
        actual = {
            path.relative_to(self.output).as_posix()
            for path in self.output.rglob("*")
            if path.is_file()
        }
        self.assertTrue(required.issubset(actual))
        self.assertFalse(any("mesh" in path.lower() for path in actual))
        self.assertFalse(any("dhexa" in path.lower() for path in actual))
        self.assertGreater((self.output / "qa" / "period_coverage.png").stat().st_size, 1000)

    def test_modem_data_package_is_complete(self) -> None:
        manifest = self._data(self._write_config("modem"))
        self.assertEqual(manifest.stages["femtic_input"].status, "passed")
        self.assertTrue((self.output / "inversion_input" / "observe.dat").is_file())

    def test_station_ids_follow_natural_name_order(self) -> None:
        source_path = self.root / "natural-order.dat"
        header = "> exp(+i\\omega t)\n> [mV/km]/[nT]\n"
        rows = [
            "1.0 S10 50.2 9.2 3000 4000 0 ZXY 1 2 0.5",
            "1.0 S2 50.1 9.1 2000 3000 0 ZXY 1 2 0.5",
            "1.0 S1 50.0 9.0 1000 2000 0 ZXY 1 2 0.5",
        ]
        source_path.write_text(header + "\n".join(rows) + "\n", encoding="ascii")
        config = self._write_config(
            "modem",
            mutate=lambda payload: payload["source"].update(  # type: ignore[union-attr]
                {"path": str(source_path)}
            ),
        )
        self._data(config)
        with (self.output / "projection" / "stations_projected.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            projected = list(csv.DictReader(stream))
        self.assertEqual([row["station_name"] for row in projected], ["S1", "S2", "S10"])
        self.assertEqual([row["station_id"] for row in projected], ["1", "2", "3"])

    def test_manifest_records_policy_and_hashes_every_owned_file(self) -> None:
        config = self._write_config(
            mutate=lambda payload: payload["selection"].update(  # type: ignore[union-attr]
                {"frequency_policy": "log_linear"}
            )
        )
        manifest = self._data(config)
        payload = json.loads(
            (self.output / "mt2femtic_data_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["command"], "data")
        self.assertEqual(payload["config_kind"], "data")
        self.assertEqual(
            payload["resolved_config"]["selection"]["frequency_policy"],
            "log_linear",
        )
        self.assertEqual(set(payload["stages"]), set(manifest.stages))
        source_path = str((self.root / "edi" / "S01.edi").resolve())
        self.assertEqual(
            payload["input_hashes"][source_path],
            hashlib.sha256((self.root / "edi" / "S01.edi").read_bytes()).hexdigest(),
        )
        owned = {
            path.relative_to(self.output).as_posix()
            for path in self.output.rglob("*")
            if path.is_file() and path.name != "mt2femtic_data_manifest.json"
        }
        self.assertEqual(set(payload["output_hashes"]), owned)
        for relative, expected in payload["output_hashes"].items():
            self.assertEqual(sha256_file(self.output / relative), expected)

    def test_failed_gate_publishes_only_log_and_manifest(self) -> None:
        config = self._write_config(
            mutate=lambda payload: payload["source"].update(  # type: ignore[union-attr]
                {"path": str(self.root / "missing")}
            )
        )
        manifest = self._data(config)
        self.assertEqual(manifest.stages["source"].status, "failed")
        files = {
            path.name for path in self.output.iterdir() if path.is_file()
        }
        self.assertEqual(
            files, {"mt2femtic_data.log", "mt2femtic_data_manifest.json"}
        )

    def test_resume_verifies_config_and_all_output_hashes(self) -> None:
        config = self._write_config(
            mutate=lambda payload: payload["run"].update(  # type: ignore[union-attr]
                {"resume": True}
            )
        )
        first = self._data(config)
        second = self._data(config)
        self.assertEqual(first.output_hashes, second.output_hashes)
        (self.output / "inversion_input" / "observe.dat").write_text(
            "changed\n", encoding="ascii"
        )
        with self.assertRaisesRegex(ValueError, "Resume output hash mismatch"):
            self._data(config)

    def test_overwrite_requires_owned_output_with_same_dataset(self) -> None:
        self.output.mkdir()
        (self.output / "untracked.txt").write_text("keep", encoding="utf-8")
        config = self._write_config(
            mutate=lambda payload: payload["run"].update(  # type: ignore[union-attr]
                {"overwrite": True}
            )
        )
        with self.assertRaisesRegex(ValueError, "without mt2femtic_data_manifest"):
            self._data(config)
        self.assertTrue((self.output / "untracked.txt").is_file())


if __name__ == "__main__":
    unittest.main()
