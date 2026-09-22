from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mt2femtic.cli import build_parser, main
from mt2femtic.manifest import DATA_STAGE_NAMES, RunManifest
from mt2femtic.model import StageResult


class CliTests(unittest.TestCase):
    def test_exact_public_argument_contract(self) -> None:
        parser = build_parser()
        data = parser.parse_args(
            ["data", "--config", "data.json", "--output", "data-output"]
        )
        self.assertEqual(data.command, "data")
        mesh = parser.parse_args(
            [
                "mesh",
                "--config",
                "mesh.json",
                "--data",
                "data-output",
                "--output",
                "mesh-output",
            ]
        )
        self.assertEqual(mesh.data, Path("data-output"))
        validate = parser.parse_args(
            ["validate", "--config", "data.json", "--output", "validation"]
        )
        self.assertIsNone(validate.data)

    def test_data_rejects_data_argument_and_mesh_requires_it(self) -> None:
        parser = build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as data_error:
                parser.parse_args(
                    [
                        "data",
                        "--config",
                        "data.json",
                        "--data",
                        "input",
                        "--output",
                        "output",
                    ]
                )
            with self.assertRaises(SystemExit) as mesh_error:
                parser.parse_args(
                    ["mesh", "--config", "mesh.json", "--output", "output"]
                )
        self.assertEqual(data_error.exception.code, 2)
        self.assertEqual(mesh_error.exception.code, 2)

    @patch("mt2femtic.cli.data")
    def test_pass_returns_zero_and_prints_manifest(self, data_mock) -> None:
        manifest = RunManifest(
            "unit", command="data", config_kind="data", stage_names=DATA_STAGE_NAMES
        )
        manifest.record(StageResult("source", "passed", {}, {}, "ok"))
        data_mock.return_value = manifest
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                ["data", "--config", "data.json", "--output", "data-output"]
            )
        self.assertEqual(code, 0)
        self.assertIn("status=passed", stdout.getvalue())
        self.assertIn("mt2femtic_data_manifest.json", stdout.getvalue())

    @patch("mt2femtic.cli.validate")
    def test_recorded_failure_returns_one_with_failed_gate(self, validate_mock) -> None:
        manifest = RunManifest(
            "unit",
            command="validate",
            config_kind="data",
            stage_names=DATA_STAGE_NAMES,
        )
        manifest.record(StageResult("source", "failed", {}, {}, "missing input"))
        validate_mock.return_value = manifest
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                ["validate", "--config", "data.json", "--output", "validation"]
            )
        self.assertEqual(code, 1)
        self.assertIn("failed_gate=source", stdout.getvalue())

    def test_ordinary_error_returns_one_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "data",
                        "--config",
                        str(Path(temporary) / "missing.json"),
                        "--output",
                        str(Path(temporary) / "output"),
                    ]
                )
        self.assertEqual(code, 1)
        self.assertIn("command=data status=failed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
