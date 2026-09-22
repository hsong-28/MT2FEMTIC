from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mt2femtic.manifest import sha256_file

from tests.test_dhexa import write_fake_products
from tests.test_femtic_io import inventory, write_femtic_trio
from tests.test_mesh_config import valid_mesh_config_payload


class MeshPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.generator = self.root / "makeDHexaMesh"
        self.generator.write_bytes(b"fake-generator")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_config(self, name: str = "mesh", mutate=None) -> Path:
        payload = valid_mesh_config_payload()
        generator = payload["generator"]
        axes = payload["mesh"]["configured_axes"]  # type: ignore[index]
        assert isinstance(generator, dict)
        assert isinstance(axes, dict)
        generator.update(
            {
                "path": str(self.generator),
                "sha256": hashlib.sha256(self.generator.read_bytes()).hexdigest(),
                "timeout_s": 30,
            }
        )
        axes.update(
            {
                "x_max_km": 2.0,
                "x_uniform_limit_km": 1.0,
                "x_spacing_km": 1.0,
                "y_max_km": 2.0,
                "y_uniform_limit_km": 1.0,
                "y_spacing_km": 1.0,
                "z_max_km": 2.0,
                "z_spacing_km": 0.5,
            }
        )
        if mutate is not None:
            mutate(payload)
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _fake_run(identity, work_dir: Path):
        write_fake_products(Path(work_dir))
        stdout = "Start Mesh Generation. Version v1.6.1\nEnd Mesh Generation.\n"
        (Path(work_dir) / "meshgen.stdout").write_text(stdout, encoding="utf-8")
        (Path(work_dir) / "meshgen.stderr").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess([str(identity.path)], 0, stdout, "")

    def _native_data(self) -> Path:
        data_root = self.root / "native-data"
        inversion_input = data_root / "inversion_input"
        write_femtic_trio(inversion_input)
        hashes = {
            f"inversion_input/{name}": sha256_file(inversion_input / name)
            for name in ("observe.dat", "obs_site.dat", "distortion_iter0.dat")
        }
        (data_root / "mt2femtic_data_manifest.json").write_text(
            json.dumps({"output_hashes": hashes}), encoding="utf-8"
        )
        return data_root

    def _mesh(self, config: Path, data_root: Path, output: Path):
        from mt2femtic.mesh_pipeline import mesh

        return mesh(config, data_root, output)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_native_data_package_generates_separate_mesh_output(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self._native_data()
        before = inventory(data_root)
        output = self.root / "mesh-output"
        manifest = self._mesh(self._write_config(), data_root, output)
        self.assertEqual(manifest.stages["products"].status, "passed")
        required = {
            "input_audit.json",
            "dhexa/obs_site.dat",
            "dhexa/meshgen.inp",
            "dhexa/mesh.dat",
            "dhexa/resistivity_block_iter0.dat",
            "dhexa/MeshData.vtk",
            "dhexa/meshgen.stdout",
            "dhexa/meshgen.stderr",
            "mt2femtic_mesh.log",
            "mt2femtic_mesh_manifest.json",
        }
        actual = {
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file()
        }
        self.assertTrue(required.issubset(actual))
        self.assertEqual(inventory(data_root), before)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_none_refinement_stages_empty_observation_contract(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self._native_data()
        before = inventory(data_root)
        config = self._write_config(
            "no-refinement",
            lambda payload: payload["mesh"].update(  # type: ignore[union-attr]
                {"observation_refinement": "none"}
            ),
        )
        output = self.root / "mesh-output"
        manifest = self._mesh(config, data_root, output)
        self.assertEqual(manifest.stages["products"].status, "passed")
        self.assertEqual(
            (output / "dhexa" / "obs_site.dat").read_text(encoding="ascii"),
            "0\n0\n",
        )
        audit = json.loads((output / "input_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["observation_refinement"], "none")
        self.assertEqual(inventory(data_root), before)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_external_femtic_directory_needs_no_manifest(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        before = inventory(data_root)
        output = self.root / "mesh-output"
        result = self._mesh(self._write_config(), data_root, output)
        self.assertEqual(result.stages["products"].status, "passed")
        audit = json.loads((output / "input_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["layout"], "external")
        self.assertEqual(inventory(data_root), before)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_bad_data_fails_before_generator_and_publishes_only_records(
        self, run_mock
    ) -> None:
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        os.unlink(data_root / "distortion_iter0.dat")
        output = self.root / "mesh-output"
        result = self._mesh(self._write_config(), data_root, output)
        self.assertEqual(result.stages["femtic_import"].status, "failed")
        run_mock.assert_not_called()
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {"mt2femtic_mesh.log", "mt2femtic_mesh_manifest.json"},
        )

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_one_data_root_supports_two_independent_meshes(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        before = inventory(data_root)
        first = self._mesh(self._write_config("mesh-a"), data_root, self.root / "a")
        second_config = self._write_config(
            "mesh-b",
            lambda payload: payload.update({"mesh_id": "unit-mesh-b"}),
        )
        second = self._mesh(second_config, data_root, self.root / "b")
        self.assertEqual(first.stages["products"].status, "passed")
        self.assertEqual(second.stages["products"].status, "passed")
        self.assertEqual(inventory(data_root), before)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_explicit_topography_is_staged_and_native_absence_fails(
        self, run_mock
    ) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        topography = self.root / "normalized_topography.dat"
        topography.write_text(
            "-3 -3 0\n-3 3 0\n3 -3 0\n3 3 0\n", encoding="ascii"
        )
        file_config = self._write_config(
            "file-topography",
            lambda payload: payload["topography"].update(  # type: ignore[union-attr]
                {"mode": "file", "path": str(topography)}
            ),
        )
        passed = self._mesh(file_config, data_root, self.root / "file-output")
        self.assertEqual(passed.stages["products"].status, "passed")
        self.assertTrue(
            (self.root / "file-output" / "dhexa" / "topography.dat").is_file()
        )
        native_config = self._write_config(
            "native-topography",
            lambda payload: payload["topography"].update(  # type: ignore[union-attr]
                {"mode": "native"}
            ),
        )
        failed = self._mesh(native_config, data_root, self.root / "native-output")
        self.assertEqual(failed.stages["topography"].status, "failed")

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_resume_verifies_input_and_output_hashes(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        config = self._write_config(
            mutate=lambda payload: payload["run"].update(  # type: ignore[union-attr]
                {"resume": True}
            )
        )
        output = self.root / "mesh-output"
        first = self._mesh(config, data_root, output)
        second = self._mesh(config, data_root, output)
        self.assertEqual(first.output_hashes, second.output_hashes)
        run_mock.assert_called_once()
        (data_root / "observe.dat").write_text("changed\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Resume input hash mismatch"):
            self._mesh(config, data_root, output)

        write_femtic_trio(data_root)
        (output / "dhexa" / "mesh.dat").write_text("changed\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "Resume output hash mismatch"):
            self._mesh(config, data_root, output)

    @patch("mt2femtic.mesh_pipeline.run_generator")
    def test_manifest_records_generator_and_all_hashes(self, run_mock) -> None:
        run_mock.side_effect = self._fake_run
        data_root = self.root / "external"
        write_femtic_trio(data_root)
        output = self.root / "mesh-output"
        self._mesh(self._write_config(), data_root, output)
        payload = json.loads(
            (output / "mt2femtic_mesh_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["command"], "mesh")
        self.assertEqual(payload["config_kind"], "mesh")
        self.assertEqual(payload["mesh_id"], "unit-mesh")
        self.assertEqual(payload["executable"]["version"], "v1.6.1")
        for relative, expected in payload["output_hashes"].items():
            self.assertEqual(sha256_file(output / relative), expected)


if __name__ == "__main__":
    unittest.main()
