from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest


class InstallationTests(unittest.TestCase):
    def test_mt2femtic_package_is_importable(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("mt2femtic"))

    def test_module_entry_point_has_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "mt2femtic", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("validate", completed.stdout)
        self.assertIn("data", completed.stdout)
        self.assertIn("mesh", completed.stdout)

    def test_each_public_command_has_help(self) -> None:
        for command in ("validate", "data", "mesh"):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, "-m", "mt2femtic", command, "--help"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
