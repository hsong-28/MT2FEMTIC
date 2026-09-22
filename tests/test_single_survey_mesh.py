from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mt2femtic.manifest import sha256_file
from mt2femtic.single_survey import (
    project_sites_stage,
    read_edi_stage,
    select_data_stage,
)
from mt2femtic.single_survey.mesh import write_meshgen_stage
from tests.test_mesh_config import valid_mesh_config_payload
from tests.test_single_survey_case import valid_payload


ROOT = Path(__file__).parents[1]
EDI_FIXTURE = ROOT / "tests/fixtures/edi/plus_iwt.edi"


def mesh_payload() -> dict[str, object]:
    payload = valid_mesh_config_payload()
    axes = payload["mesh"]["configured_axes"]  # type: ignore[index]
    assert isinstance(axes, dict)
    axes.update({"x_max_km": 1000.0, "y_max_km": 1000.0, "z_max_km": 10.0})
    return payload


class SingleSurveyMeshTests(unittest.TestCase):
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
        select_data_stage(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_mesh_config(self, payload: dict[str, object] | None = None) -> None:
        (self.root / "mesh.json").write_text(
            json.dumps(payload or mesh_payload(), indent=2) + "\n",
            encoding="utf-8",
        )

    def test_stage_writes_complete_inputs_without_running_generator(self) -> None:
        self.write_mesh_config()
        source_obs = self.root / "3-DataSelected4Inv/obs_site.dat"
        report = write_meshgen_stage(self.root)
        output = self.root / "4-MeshGeneration"
        self.assertEqual(report["execution_status"], "not_run")
        self.assertEqual(report["generator"]["status"], "declared_not_verified")
        self.assertEqual((output / "obs_site.dat").read_bytes(), source_obs.read_bytes())
        self.assertTrue((output / "meshgen.inp").is_file())
        self.assertTrue((output / "stage-manifest.json").is_file())
        for name in ("mesh.dat", "MeshData.vtk", "meshgen.stdout", "meshgen.stderr"):
            self.assertFalse((output / name).exists())
        self.assertEqual(
            set(report["output_hashes"]), {"meshgen.inp", "obs_site.dat"}
        )
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_axes_and_keyword_order_preserve_dhexa_contract(self) -> None:
        self.write_mesh_config()
        report = write_meshgen_stage(self.root)
        lines = (self.root / "4-MeshGeneration/meshgen.inp").read_text(
            encoding="ascii"
        ).splitlines()
        keywords = [
            "DIVISION_NUMBERS", "X_COORDINATES", "Y_COORDINATES", "Z_COORDINATES",
            "INITIAL_RESISTIVITY", "AIR_RESISTIVITY", "SEA_RESISTIVITY",
            "ANOMALIES", "END",
        ]
        self.assertEqual([lines.index(key) for key in keywords], sorted(lines.index(key) for key in keywords))
        nx, ny, nz = report["axis_divisions"]
        x0 = lines.index("X_COORDINATES") + 1
        y0 = lines.index("Y_COORDINATES") + 1
        z0 = lines.index("Z_COORDINATES") + 1
        x = [float(value) for value in lines[x0:y0 - 1]]
        y = [float(value) for value in lines[y0:z0 - 1]]
        z = [float(value) for value in lines[z0:z0 + nz + 1]]
        self.assertEqual((len(x) - 1, len(y) - 1, len(z) - 1), (nx, ny, nz))
        self.assertTrue(all(a < b for a, b in zip(x, x[1:])))
        self.assertTrue(all(a < b for a, b in zip(y, y[1:])))
        self.assertTrue(all(a < b for a, b in zip(z, z[1:])))
        self.assertEqual(x, [-value for value in reversed(x)])
        self.assertEqual(y, [-value for value in reversed(y)])
        self.assertLess(z[0], 0.0)
        self.assertIn(0.0, z)
        self.assertGreater(z[-1], 0.0)

    def test_stage_copies_file_topography_and_hashes_all_inputs(self) -> None:
        payload = mesh_payload()
        topography = payload["topography"]
        assert isinstance(topography, dict)
        topography.update({"mode": "file", "path": "inputs/topography.dat"})
        source = self.root / "inputs/topography.dat"
        source.parent.mkdir()
        source.write_text("0 0 0\n", encoding="ascii")
        self.write_mesh_config(payload)
        report = write_meshgen_stage(self.root)
        output = self.root / "4-MeshGeneration"
        self.assertEqual((output / "topography.dat").read_bytes(), source.read_bytes())
        self.assertEqual(report["input_hashes"]["inputs/topography.dat"], sha256_file(source))
        self.assertIn("TOPO\ntopography.dat\n", (output / "meshgen.inp").read_text(encoding="ascii"))

    def test_stage_refuses_overwrite(self) -> None:
        self.write_mesh_config()
        write_meshgen_stage(self.root)
        with self.assertRaisesRegex(FileExistsError, "4-MeshGeneration"):
            write_meshgen_stage(self.root)

    def test_stage_rejects_changed_selected_output(self) -> None:
        self.write_mesh_config()
        path = self.root / "3-DataSelected4Inv/observe.dat"
        path.write_text(path.read_text(encoding="ascii") + "changed\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Selection-stage output hash mismatch"):
            write_meshgen_stage(self.root)
        self.assertFalse((self.root / "4-MeshGeneration").exists())

    def test_stage_rejects_station_outside_mesh(self) -> None:
        payload = mesh_payload()
        axes = payload["mesh"]["configured_axes"]  # type: ignore[index]
        assert isinstance(axes, dict)
        axes.update({"x_max_km": 10.0, "y_max_km": 10.0})
        self.write_mesh_config(payload)
        with self.assertRaisesRegex(ValueError, "outside the DHEXA mesh"):
            write_meshgen_stage(self.root)

    def test_script_prints_json_report(self) -> None:
        self.write_mesh_config()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/04_write_meshgen.py"), "--root", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(json.loads(result.stdout)["execution_status"], "not_run")


if __name__ == "__main__":
    unittest.main()
