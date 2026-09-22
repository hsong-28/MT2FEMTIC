#!/usr/bin/env python3
"""Verify exact Broken Hill H2 mesh products and basic dimensions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mesh_counts(path: Path) -> tuple[int, int]:
    with path.open("r", encoding="ascii") as stream:
        if stream.readline().strip() != "DHEXA":
            raise ValueError("mesh.dat is not DHEXA")
        nodes = int(stream.readline())
        for _ in range(nodes):
            stream.readline()
        return nodes, int(stream.readline())


def verify(output: Path, expected_path: Path) -> dict[str, object]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))["h2"]
    missing = [name for name in (*expected["outputs"], "meshgen.stdout", "meshgen.stderr")
               if not (output / name).is_file()]
    mismatches = []
    if not missing:
        for name, digest in expected["outputs"].items():
            if sha256(output / name) != digest:
                mismatches.append(name)
        nodes, elements = mesh_counts(output / "mesh.dat")
        with (output / "resistivity_block_iter0.dat").open("r", encoding="ascii") as stream:
            model_elements, parameters = map(int, stream.readline().split())
        counts = {
            "node_count": nodes,
            "element_count": elements,
            "model_element_count": model_elements,
            "parameter_count": parameters,
            "active_parameter_count": parameters - 1,
        }
        count_errors = [key for key in ("node_count", "element_count", "parameter_count", "active_parameter_count")
                        if counts[key] != expected[key]]
        stderr_empty = (output / "meshgen.stderr").stat().st_size == 0
        end_marker = expected["end_marker"] in (output / "meshgen.stdout").read_text(encoding="utf-8", errors="replace")
    else:
        counts, count_errors, stderr_empty, end_marker = {}, [], False, False
    passed = not any((missing, mismatches, count_errors, not stderr_empty, not end_marker))
    return {
        "passed": passed,
        "output": str(output.resolve()),
        "missing": missing,
        "hash_mismatches": mismatches,
        "count_errors": count_errors,
        "counts": counts,
        "stderr_empty": stderr_empty,
        "end_marker_present": end_marker,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.output_dir, args.expected)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
