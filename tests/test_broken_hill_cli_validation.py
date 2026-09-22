from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "broken_hill"
SCRIPTS = EXAMPLE / "scripts"
EDI_FIXTURE = ROOT / "tests" / "fixtures" / "edi" / "plus_iwt.edi"
MODEM_FIXTURE = ROOT / "tests" / "fixtures" / "modem" / "survey.dat"


class BrokenHillCliValidationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "PowerShell runner requires Windows")
    def test_runner_uses_active_environment_without_python_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            example = root / "examples" / "broken_hill"
            shutil.copytree(EXAMPLE, example, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "src" / "mt2femtic", root / "src" / "mt2femtic",
                            ignore=shutil.ignore_patterns("__pycache__"))
            active = root / "venv"
            venv.EnvBuilder(system_site_packages=True).create(active)
            scripts = active / "Scripts"
            (scripts / "py.cmd").write_text("@echo Python launcher must not be used\n@exit /b 91\n")
            environment = os.environ.copy()
            environment.update({"VIRTUAL_ENV": str(active), "PATH": str(scripts) + os.pathsep + environment["PATH"]})
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(example / "run.ps1"), "-RunName", "active-environment"],
                cwd=root, env=environment, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(str(scripts / "python.exe"), result.stdout)
            self.assertIn('"data_baseline": "PASS"', result.stdout)

    def test_source_archive_sha256_is_complete(self) -> None:
        provenance = (EXAMPLE / "SOURCE_PROVENANCE.md").read_text(encoding="utf-8")
        self.assertIn(
            "c0b2317fa3312326218362598b7694e5df45450e7cd9fe3c336ffdc37a9252d0",
            provenance,
        )

    def test_manual_commands_write_the_outputs_checked_by_the_verifier(self) -> None:
        readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
        self.assertIn("--output data\\modem", readme)
        self.assertIn("--output data\\edi", readme)
        self.assertIn("--output comparison", readme)
        self.assertNotIn("--output review\\data-modem", readme)
        self.assertNotIn("--output review\\data-edi", readme)
        self.assertNotIn("--output review\\comparison", readme)

    def _compact_source(self, root: Path) -> Path:
        source = root / "source"
        edi = source / "input" / "edi"
        modem = source / "input" / "modem"
        edi.mkdir(parents=True)
        modem.mkdir(parents=True)
        base = EDI_FIXTURE.read_text(encoding="ascii")
        for index in (1, 2):
            text = base.replace("S01", f"BH_{index}")
            if index == 2:
                text = text.replace("LONG=9:00:00E", "LONG=9:00:30E")
            (edi / f"BH_{index}.edi").write_text(text, encoding="ascii")
        modem_lines = []
        for line in MODEM_FIXTURE.read_text(encoding="ascii").splitlines():
            if line.startswith("10.0 "):
                continue
            if " S02 " in line:
                continue
            modem_lines.append(line.replace("S01", "BH_1"))
            if " S01 " in line:
                modem_lines.append(
                    line.replace("S01", "BH_2")
                    .replace("50.0 9.0", "50.0 9.1")
                    .replace("1000.0 2000.0 750.0", "3000.0 4000.0 800.0")
                )
        (modem / "BH_31.dat").write_text(
            "\n".join(modem_lines) + "\n", encoding="ascii"
        )
        (source / "config").mkdir()
        for route in ("edi", "modem"):
            config = json.loads((EXAMPLE / "config" / f"{route}.json").read_text())
            config["selection"]["periods_s"] = [1.0]
            (source / "config" / f"{route}.json").write_text(json.dumps(config))
        return source

    def _run(self, arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dual_source_helpers_use_public_cli_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._compact_source(root)
            source_before = {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file()
            }
            release = root / "release"
            for source_name in ("modem", "edi"):
                result = self._run(
                    [
                        "-m",
                        "mt2femtic",
                        "data",
                        "--config",
                        str(source / "config" / f"{source_name}.json"),
                        "--output",
                        str(release / "data" / source_name),
                    ],
                    root,
                )
                self.assertEqual(
                    result.returncode, 0, f"stdout={result.stdout}\nstderr={result.stderr}"
                )
                self.assertIn("status=passed", result.stdout)
            comparison = self._run(
                [
                    str(SCRIPTS / "convert_and_compare.py"),
                    "--modem-output",
                    str(release / "data" / "modem"),
                    "--edi-output",
                    str(release / "data" / "edi"),
                    "--output",
                    str(release / "comparison"),
                ],
                root,
            )
            self.assertEqual(comparison.returncode, 0, comparison.stderr)
            summary = json.loads(
                (release / "comparison" / "validation_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["station_identity"]["matched_count"], 2)
            self.assertEqual(summary["frequencies"]["target_count"], 1)
            self.assertIn("response_differences_by_component", summary)
            self.assertIn("standard_error_differences_by_component", summary)
            self.assertIn("coordinate_horizontal_difference_m", summary)
            self.assertIn("source_hashes", summary["modem"])
            self.assertIn("source_hashes", summary["edi"])
            modem_manifest = json.loads(
                (release / "data" / "modem" / "mt2femtic_data_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            edi_manifest = json.loads(
                (release / "data" / "edi" / "mt2femtic_data_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                modem_manifest["resolved_config"]["selection"]["frequency_policy"],
                "exact",
            )
            self.assertEqual(
                modem_manifest["resolved_config"]["selection"][
                    "impedance_error_floor_fraction"
                ],
                0.0,
            )
            self.assertEqual(
                edi_manifest["resolved_config"]["selection"]["frequency_policy"],
                "log_linear",
            )
            self.assertEqual(
                edi_manifest["resolved_config"]["selection"][
                    "impedance_error_floor_fraction"
                ],
                0.05,
            )
            self.assertTrue(
                edi_manifest["resolved_config"]["source"][
                    "allow_time_convention_override"
                ]
            )
            modem_hash = hashlib.sha256(
                (source / "input" / "modem" / "BH_31.dat").read_bytes()
            ).hexdigest()
            baseline = {"data_outputs": {
                route: {
                    name: hashlib.sha256(
                        (release / "data" / route / "inversion_input" / name).read_bytes()
                    ).hexdigest()
                    for name in ("observe.dat", "obs_site.dat", "distortion_iter0.dat")
                }
                for route in ("edi", "modem")
            }}
            (source / "expected.json").write_text(json.dumps(baseline))
            verification_args = [
                    str(SCRIPTS / "verify_outputs.py"),
                    "--root",
                    str(release),
                    "--example",
                    str(source),
                    "--write-checksums",
                    "--expected-stations",
                    "2",
                    "--expected-frequencies",
                    "1",
                    "--expected-matched-complex",
                    "6",
                    "--expected-modem-sha256",
                    modem_hash,
                ]
            verification = self._run(verification_args, root)
            self.assertEqual(
                verification.returncode,
                0,
                f"stdout={verification.stdout}\nstderr={verification.stderr}",
            )
            self.assertIn('"status": "PASS"', verification.stdout)
            self.assertEqual(
                source_before,
                {
                    path.relative_to(source).as_posix(): path.read_bytes()
                    for path in source.rglob("*")
                    if path.is_file() and path.name != "expected.json"
                },
            )
            baseline["data_outputs"]["modem"]["observe.dat"] = "0" * 64
            (source / "expected.json").write_text(json.dumps(baseline))
            rejected = self._run(verification_args, root)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Baseline mismatch: modem/observe.dat", rejected.stderr)

    def test_comparison_script_does_not_import_private_mt2femtic_modules(self) -> None:
        text = (SCRIPTS / "convert_and_compare.py").read_text(encoding="utf-8")
        self.assertNotIn("from mt2femtic", text)
        self.assertNotIn("import mt2femtic", text)


if __name__ == "__main__":
    unittest.main()
