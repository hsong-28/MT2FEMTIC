from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mt2femtic.single_survey.case import check_input, paths_for


ROOT = Path(__file__).parents[1]


def valid_payload(source_path: str = "0-EDI") -> dict[str, object]:
    return {
        "schema_version": 1,
        "config_kind": "data",
        "dataset_id": "single-survey-test",
        "source": {
            "type": "edi",
            "path": source_path,
            "impedance_unit": "mv_per_km_per_nt",
            "time_convention": "exp_plus_iwt",
            "allow_time_convention_override": True,
            "edi_pattern": "*.edi",
        },
        "coordinates": {
            "geographic_crs": "EPSG:4326",
            "projected_crs": "EPSG:32633",
            "origin_easting_m": 500000.0,
            "origin_northing_m": 6000000.0,
            "model_axis_azimuth_deg": 0.0,
            "vertical_datum_elevation_m": 0.0,
            "round_trip_tolerance_m": 0.01,
            "modem_axis_convention": "north_east",
        },
        "selection": {
            "frequency_policy": "exact",
            "periods_s": [1.0],
            "relative_tolerance": 0.001,
            "impedance_error_floor_fraction": 0.05,
            "vtf_error_floor_absolute": 0.03,
            "max_vtf_period_s": 10000.0,
        },
        "topography": {"enabled": False, "path": None, "columns": None},
        "femtic": {
            "observation_refinement": {
                "radius_km": 0.5,
                "level": 1,
                "weight": 0.5,
            }
        },
        "run": {"overwrite": False, "resume": False},
    }


class SingleSurveyCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_case(self, payload: dict[str, object], add_edi: bool = True) -> None:
        (self.root / "survey.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        if add_edi:
            edi = self.root / "0-EDI"
            edi.mkdir()
            (edi / "station.edi").write_text(">HEAD\n", encoding="ascii")

    def test_paths_have_one_numbered_layer(self) -> None:
        paths = paths_for(self.root)
        self.assertEqual(paths.edi, self.root.resolve() / "0-EDI")
        self.assertEqual(paths.original, self.root.resolve() / "1-DataOri")
        self.assertEqual(paths.projection, self.root.resolve() / "2-Projection")
        self.assertEqual(paths.selected, self.root.resolve() / "3-DataSelected4Inv")
        self.assertEqual(paths.mesh, self.root.resolve() / "4-MeshGeneration")

    def test_check_input_reports_edi_without_creating_outputs(self) -> None:
        self.write_case(valid_payload())
        self.assertEqual(
            check_input(self.root),
            {
                "dataset_id": "single-survey-test",
                "edi_file_count": 1,
                "edi_inventory": "pattern",
                "source_type": "edi",
                "station_name_source": "edi_metadata",
            },
        )
        paths = paths_for(self.root)
        self.assertFalse(any(path.exists() for path in paths.outputs))

    def test_check_input_rejects_modem_until_edi_stage_is_accepted(self) -> None:
        payload = valid_payload()
        assert isinstance(payload["source"], dict)
        payload["source"]["type"] = "modem"
        self.write_case(payload)
        with self.assertRaisesRegex(ValueError, "EDI-only implementation stage"):
            check_input(self.root)

    def test_check_input_requires_exact_edi_directory(self) -> None:
        self.write_case(valid_payload("input/edi"))
        with self.assertRaisesRegex(ValueError, "source.path must be 0-EDI"):
            check_input(self.root)

    def test_check_input_requires_a_matching_edi_file(self) -> None:
        self.write_case(valid_payload(), add_edi=False)
        (self.root / "0-EDI").mkdir()
        with self.assertRaisesRegex(FileNotFoundError, "No EDI files match"):
            check_input(self.root)

    def test_check_input_rejects_temporal_fields(self) -> None:
        payload = valid_payload()
        payload["year"] = "2026"
        self.write_case(payload)
        with self.assertRaisesRegex(ValueError, "Temporal field is not allowed: year"):
            check_input(self.root)

    def test_script_prints_json_report(self) -> None:
        self.write_case(valid_payload())
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/00_check_input.py"), "--root", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(json.loads(result.stdout)["edi_file_count"], 1)

    def test_inventory_file_controls_selection_and_order(self) -> None:
        payload = valid_payload()
        source = payload["source"]
        assert isinstance(source, dict)
        source["edi_list_file"] = "0-EDI/edi_list_3D.txt"
        source["station_name_source"] = "file_stem"
        self.write_case(payload, add_edi=False)
        edi = self.root / "0-EDI"
        edi.mkdir()
        for name in ("a.edi", "b.edi", "unused.edi"):
            (edi / name).write_text(">HEAD\n", encoding="ascii")
        (edi / "edi_list_3D.txt").write_text("2\nb.edi\na.edi\n", encoding="ascii")
        report = check_input(self.root)
        self.assertEqual(report["edi_file_count"], 2)
        self.assertEqual(report["edi_inventory"], "edi_list_3D.txt")
        self.assertEqual(report["station_name_source"], "file_stem")

    def test_inventory_count_must_match_rows(self) -> None:
        payload = valid_payload()
        source = payload["source"]
        assert isinstance(source, dict)
        source["edi_list_file"] = "0-EDI/edi_list_3D.txt"
        self.write_case(payload)
        (self.root / "0-EDI/edi_list_3D.txt").write_text(
            "2\nstation.edi\n", encoding="ascii"
        )
        with self.assertRaisesRegex(ValueError, "declares 2 files but lists 1"):
            check_input(self.root)


if __name__ == "__main__":
    unittest.main()
