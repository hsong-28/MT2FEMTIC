"""Regression tests for supported module import order."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


class ModuleImportTests(unittest.TestCase):
    def test_dhexa_can_be_imported_directly(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from mt2femtic.dhexa import _air_coordinates; "
                "print(_air_coordinates(1, 0.25, 150.0))",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
