from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_package import verify_package, write_checksum_manifest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_package(root: Path) -> None:
    _write(root / "LICENSE", "Test license\n")
    _write(root / "README.md", "# MT2FEMTIC\n")
    _write(root / "THIRD_PARTY_NOTICES.md", "# Third-party notices\n")
    _write(
        root / "CITATION.cff",
        'cff-version: 1.2.0\ntitle: "MT2FEMTIC"\nauthors:\n  - name: "Test Author"\nversion: 0.1.0\n',
    )
    _write(
        root / "pyproject.toml",
        '[project]\nname = "mt2femtic"\nversion = "0.1.0"\n',
    )
    _write(root / "src" / "mt2femtic" / "__init__.py", '__version__ = "0.1.0"\n')
    _write(root / "tests" / "fixtures" / "femticpy" / "LICENSE", "Fixture license\n")


class VerifyPackageTests(unittest.TestCase):
    def test_missing_third_party_fixture_license_is_a_release_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            (root / "tests" / "fixtures" / "femticpy" / "LICENSE").unlink()
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertIn(
            "missing required file: tests/fixtures/femticpy/LICENSE",
            report["errors"],
        )

    def test_local_tooling_artifact_is_a_release_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "docs" / ".local-tool" / "notes.md", "Local notes\n")
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertIn(
            "local tooling artifact: docs/.local-tool/notes.md",
            report["errors"],
        )

    def test_missing_license_is_a_release_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            (root / "LICENSE").unlink()
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertIn("missing required file: LICENSE", report["errors"])

    def test_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "src" / "mt2femtic" / "__init__.py", '__version__ = "0.2.0"\n')
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertTrue(any("version mismatch" in item for item in report["errors"]))

    def test_developer_absolute_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "README.md", "D:\\private\\dataset\n")
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertTrue(any("developer-specific path" in item for item in report["errors"]))

    def test_checksum_manifest_must_cover_every_release_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            readme = root / "README.md"
            digest = hashlib.sha256(readme.read_bytes()).hexdigest()
            _write(root / "SHA256SUMS.txt", f"{digest}  README.md\n")
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertTrue(any("checksum coverage mismatch" in item for item in report["errors"]))

    def test_minimal_complete_package_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            report = verify_package(root)
        self.assertTrue(report["passed"], json.dumps(report, indent=2))

    def test_cache_files_warn_but_do_not_block_release_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "src" / "mt2femtic" / "__pycache__" / "module.pyc", "cache")
            report = verify_package(root)
        self.assertTrue(report["passed"], json.dumps(report, indent=2))
        self.assertTrue(any("cache files present" in item for item in report["warnings"]))

    def test_generated_checksum_manifest_covers_release_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            entry_count = write_checksum_manifest(root)
            report = verify_package(root)
        self.assertEqual(entry_count, 7)
        self.assertTrue(report["passed"], json.dumps(report, indent=2))
        self.assertEqual(report["checksum_entry_count"], 7)

    def test_build_metadata_is_excluded_from_release_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "src" / "mt2femtic.egg-info" / "PKG-INFO", "generated")
            _write(root / "PKG-INFO", "generated")
            _write(root / "setup.cfg", "generated")
            report = verify_package(root)
        self.assertTrue(report["passed"], json.dumps(report, indent=2))
        self.assertEqual(report["release_file_count"], 7)

    def test_checkout_outputs_are_ignored_only_in_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "reports" / "local.json", r'"path": "D:\private\data"' + "\n")
            release_report = verify_package(root)
            (root / ".git").mkdir()
            checkout_report = verify_package(root)
        self.assertFalse(release_report["passed"])
        self.assertTrue(checkout_report["passed"], json.dumps(checkout_report, indent=2))

    def test_collective_citation_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(
                root / "CITATION.cff",
                'cff-version: 1.2.0\ntitle: "MT2FEMTIC"\nauthors:\n'
                '  - name: "MT2FEMTIC contributors"\nversion: 0.1.0\n',
            )
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertIn("citation author metadata is still a placeholder", report["errors"])

    def test_repository_url_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _minimal_package(root)
            _write(root / "README.md", "git clone YOUR_REPOSITORY_URL\n")
            report = verify_package(root)
        self.assertFalse(report["passed"])
        self.assertIn("unresolved placeholder: YOUR_REPOSITORY_URL", report["errors"])


if __name__ == "__main__":
    unittest.main()
