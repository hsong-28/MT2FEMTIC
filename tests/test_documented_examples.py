from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mt2femtic.config import DataConfig, MeshCommandConfig, load_config


ROOT = Path(__file__).parents[1]


class DocumentedExamplesTests(unittest.TestCase):
    def test_source_manifest_includes_femticpy_license(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("include tests/fixtures/femticpy/LICENSE", manifest)

    def test_validation_guide_covers_every_public_stage(self) -> None:
        guide_path = ROOT / "docs" / "validation.md"
        self.assertTrue(guide_path.is_file())
        guide = guide_path.read_text(encoding="utf-8")
        for command in (
            "mt2femtic validate",
            "mt2femtic data",
            "mt2femtic mesh",
            "convert_and_compare.py",
            "verify_outputs.py",
            "prepare_h2_mesh.py",
            "verify_h2_mesh.py",
            "python -m build",
            "verify_package.py",
        ):
            with self.subTest(command=command):
                self.assertIn(command, guide)
        self.assertIn("exp(-i*omega*t)", guide)
        self.assertIn("X = north", guide)
        self.assertIn("Y = east", guide)

    def test_released_text_has_no_developer_paths(self) -> None:
        paths = [ROOT / "README.md"]
        paths.extend((ROOT / "docs").rglob("*.md"))
        paths.extend((ROOT / "examples" / "minimal").glob("*.json"))
        paths.extend((ROOT / "examples" / "broken_hill" / "config").glob("*.json"))
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                normalized = text.replace("\\", "/")
                self.assertNotIn("D:/", text)
                self.assertNotIn("D:\\", text)
                self.assertNotIn("/worktrees/", normalized)
                self.assertNotIn("LENOVO", text)

    def test_all_public_configs_load_strictly(self) -> None:
        configs = ROOT / "examples" / "minimal"
        edi = load_config(configs / "edi-data.json")
        modem = load_config(configs / "modem-data.json")
        mesh = load_config(configs / "dhexa-mesh.json")
        yellowstone = load_config(ROOT / "examples" / "yellowstone" / "survey.json")
        self.assertIsInstance(edi, DataConfig)
        self.assertIsInstance(modem, DataConfig)
        self.assertIsInstance(mesh, MeshCommandConfig)
        self.assertIsInstance(yellowstone, DataConfig)
        broken_hill = ROOT / "examples" / "broken_hill"
        for route in ("edi", "modem"):
            config = load_config(broken_hill / "config" / f"{route}.json")
            self.assertIsInstance(config, DataConfig)
            self.assertTrue(config.source.path.exists())
            self.assertTrue(config.source.path.is_relative_to(broken_hill))
        bh_mesh = load_config(broken_hill / "config" / "mesh.json")
        self.assertIsInstance(bh_mesh, MeshCommandConfig)
        self.assertTrue(bh_mesh.generator.path.is_file())

    def test_yellowstone_inventory_matches_source_manifest(self) -> None:
        example = ROOT / "examples" / "yellowstone"
        source = json.loads((example / "source-files.json").read_text(encoding="utf-8"))
        rows = [
            line.strip()
            for line in (example / "edi-files.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        files = source["files"]
        self.assertEqual(int(rows[0]), 98)
        self.assertEqual(rows[1:], [item["filename"] for item in files])
        self.assertEqual(len({item["site_id"] for item in files}), 98)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in files))

    def test_readme_links_to_examples_and_current_output_documentation(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative in (
            "examples/broken_hill/README.md",
            "examples/yellowstone/README.md",
            "examples/minimal/README.md",
            "examples/manual_workflow/README.md",
            "docs/validation.md",
        ):
            self.assertIn(relative, readme)
            self.assertTrue((ROOT / relative).is_file())
        guide = (ROOT / "docs" / "validation.md").read_text(encoding="utf-8")
        self.assertIn("../examples/manual_workflow/README.md", guide)
        self.assertTrue((ROOT / "examples/manual_workflow/README.md").is_file())
        for name in (
            "mt2femtic_data_manifest.json",
            "mt2femtic_mesh_manifest.json",
            "mt2femtic_validation_manifest.json",
        ):
            self.assertIn(name, guide)
        self.assertNotIn("prepare3d_manifest.json", readme)

    def test_bundled_data_quick_starts_work_outside_checkout(self) -> None:
        configs = ROOT / "examples" / "minimal"
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary)
            for source in ("edi", "modem"):
                with self.subTest(source=source):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "mt2femtic",
                            "data",
                            "--config",
                            str(configs / f"{source}-data.json"),
                            "--output",
                            str(outside / f"{source}-data-output"),
                        ],
                        cwd=outside,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("status=passed", result.stdout)

    def test_mesh_template_can_be_preflighted_with_test_generator(self) -> None:
        template = json.loads(
            (ROOT / "examples" / "minimal" / "dhexa-mesh.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("SET_PATH", template["generator"]["path"])
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary)
            data_output = outside / "data"
            data_run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mt2femtic",
                    "data",
                    "--config",
                    str(ROOT / "examples" / "minimal" / "edi-data.json"),
                    "--output",
                    str(data_output),
                ],
                cwd=outside,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(data_run.returncode, 0, data_run.stderr)
            template["generator"].update(
                {
                    "path": sys.executable,
                    "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                    "version": "test-preflight-only",
                }
            )
            config = outside / "mesh.json"
            config.write_text(json.dumps(template), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mt2femtic",
                    "validate",
                    "--config",
                    str(config),
                    "--data",
                    str(data_output),
                    "--output",
                    str(outside / "mesh-validation"),
                ],
                cwd=outside,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
