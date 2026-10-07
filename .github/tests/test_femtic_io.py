from __future__ import annotations

import json
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from mt2femtic.manifest import sha256_file


def _mt_row(frequency: float = 1.0) -> str:
    values = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 4.0, -4.0]
    errors = [0.1, 0.1, 0.2, 0.2, 0.3, 0.3, 0.4, 0.4]
    return " ".join(str(value) for value in [frequency, *values, *errors])


def _vtf_row(frequency: float = 1.0) -> str:
    values = [0.1, -0.1, 0.2, -0.2]
    errors = [0.01, 0.01, 0.02, 0.02]
    return " ".join(str(value) for value in [frequency, *values, *errors])


def write_femtic_trio(
    root: Path,
    *,
    mt_headers: tuple[str, ...] = ("1 1001 0 0",),
    include_vtf: bool = False,
    vtf_header: str = "1001 1001 0 0",
    refinement_count: int = 1,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    observe = [f"MT {len(mt_headers)}"]
    for header in mt_headers:
        observe.extend((header, "1", _mt_row()))
    if include_vtf:
        observe.extend(("VTF 1", vtf_header, "1", _vtf_row()))
    observe.append("END")
    (root / "observe.dat").write_text("\n".join(observe) + "\n", encoding="ascii")

    site_lines = [str(len(mt_headers))]
    for header in mt_headers:
        tokens = header.split()
        site_lines.extend((f"{tokens[-2]} {tokens[-1]} 0", str(refinement_count)))
        for index in range(refinement_count):
            site_lines.append(f"{index + 1} {index + 1} 0.5")
    site_lines.append("0")
    (root / "obs_site.dat").write_text(
        "\n".join(site_lines) + "\n", encoding="ascii"
    )

    distortion = [str(len(mt_headers))]
    distortion.extend(f"{header.split()[0]} 0 0 0 0 0" for header in mt_headers)
    (root / "distortion_iter0.dat").write_text(
        "\n".join(distortion) + "\n", encoding="ascii"
    )


def inventory(root: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                sha256_file(path),
            )
    return result


class FemticInputTests(unittest.TestCase):
    def _load(self, root: Path):
        self.assertIsNotNone(importlib.util.find_spec("mt2femtic.femtic_io"))
        import mt2femtic.femtic_io as femtic_io

        return femtic_io.load_femtic_input_set(root, 1.0e-9)

    def test_external_official_four_column_mt_only_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root)
            result = self._load(root)
            self.assertEqual(result.layout, "external")
            self.assertEqual(len(result.stations), 1)
            self.assertEqual(result.stations[0].station_id, 1)
            self.assertEqual(result.audit["mt_header_column_counts"], [4])
            self.assertEqual(result.audit["vtf_station_count"], 0)

    def test_individual_selector_header_forms_are_accepted(self) -> None:
        cases = (
            ("1 1001 1 0 0", "1001 1001 1 0 0", 5),
            ("1 1001 1 0 0 0", "1001 1001 1 0 0", 6),
        )
        for mt_header, vtf_header, expected_columns in cases:
            with self.subTest(mt_columns=expected_columns):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    write_femtic_trio(
                        root,
                        mt_headers=(mt_header,),
                        include_vtf=True,
                        vtf_header=vtf_header,
                    )
                    result = self._load(root)
                    self.assertEqual(
                        result.audit["mt_header_column_counts"],
                        [expected_columns],
                    )
                    self.assertEqual(result.audit["vtf_header_column_counts"], [5])

    def test_unknown_owner_element_selector_is_rejected(self) -> None:
        cases = (
            {
                "mt_headers": ("1 1001 2 0 0",),
                "include_vtf": False,
            },
            {
                "mt_headers": ("1 1001 0 0 0",),
                "include_vtf": True,
                "vtf_header": "1001 1001 2 0 0",
            },
        )
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    write_femtic_trio(root, **case)
                    with self.assertRaisesRegex(ValueError, "owner-element selector"):
                        self._load(root)

    def test_unknown_mt_electric_field_selector_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root, mt_headers=("1 1001 0 2 0 0",))
            with self.assertRaisesRegex(ValueError, "electric-field selector"):
                self._load(root)

    def test_multiple_observation_refinement_rules_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root, refinement_count=2)
            result = self._load(root)
            self.assertEqual(result.audit["observation_refinement_rule_count"], 2)

    def test_input_directory_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root, include_vtf=True)
            before = inventory(root)
            self._load(root)
            self.assertEqual(inventory(root), before)

    def test_missing_file_and_ambiguous_layout_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root)
            os.unlink(root / "distortion_iter0.dat")
            with self.assertRaisesRegex(ValueError, "Missing FEMTIC input file"):
                self._load(root)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(root)
            native = root / "inversion_input"
            write_femtic_trio(native)
            hashes = {
                f"inversion_input/{name}": sha256_file(native / name)
                for name in ("observe.dat", "obs_site.dat", "distortion_iter0.dat")
            }
            (root / "mt2femtic_data_manifest.json").write_text(
                json.dumps({"output_hashes": hashes}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                self._load(root)

    def test_complete_structure_errors_are_rejected(self) -> None:
        mutations = {
            "end marker": lambda root: (root / "observe.dat").write_text(
                (root / "observe.dat").read_text(encoding="ascii").replace(
                    "END\n", ""
                ),
                encoding="ascii",
            ),
            "coordinate mismatch": lambda root: (root / "obs_site.dat").write_text(
                (root / "obs_site.dat")
                .read_text(encoding="ascii")
                .replace("0 0 0", "0.1 0 0"),
                encoding="ascii",
            ),
            "distortion identifier": lambda root: (
                root / "distortion_iter0.dat"
            ).write_text("1\n2 0 0 0 0 0\n", encoding="ascii"),
            "nonpositive frequency": lambda root: (root / "observe.dat").write_text(
                (root / "observe.dat")
                .read_text(encoding="ascii")
                .replace(_mt_row(), _mt_row(0.0)),
                encoding="ascii",
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    write_femtic_trio(root)
                    mutate(root)
                    with self.assertRaises(ValueError):
                        self._load(root)

    def test_duplicate_mt_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_femtic_trio(
                root,
                mt_headers=("1 1001 0 0", "1 1002 1 1"),
            )
            with self.assertRaisesRegex(ValueError, "Duplicate MT station ID"):
                self._load(root)

    def test_native_manifest_hashes_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "inversion_input"
            write_femtic_trio(native)
            hashes = {
                f"inversion_input/{name}": sha256_file(native / name)
                for name in ("observe.dat", "obs_site.dat", "distortion_iter0.dat")
            }
            manifest = root / "mt2femtic_data_manifest.json"
            manifest.write_text(
                json.dumps({"output_hashes": hashes}), encoding="utf-8"
            )
            result = self._load(root)
            self.assertEqual(result.layout, "native")
            (native / "observe.dat").write_text("changed\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Native data hash mismatch"):
                self._load(root)


if __name__ == "__main__":
    unittest.main()
