#!/usr/bin/env python3
"""Independently verify a Broken Hill dual-source MT2FEMTIC release."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


EXPECTED_MODEM_SHA256 = "6f6a60ced32b8cba892bf6c51664ad9be380aec88e48ec4369c7cd8ff449e090"
REQUIRED = (
    "data/modem/inversion_input/observe.dat",
    "data/modem/inversion_input/obs_site.dat",
    "data/modem/inversion_input/distortion_iter0.dat",
    "data/modem/projection/stations_projected.csv",
    "data/modem/qa/station_topography.png",
    "data/modem/qa/period_coverage.png",
    "data/modem/mt2femtic_data_manifest.json",
    "data/edi/inversion_input/observe.dat",
    "data/edi/inversion_input/obs_site.dat",
    "data/edi/inversion_input/distortion_iter0.dat",
    "data/edi/projection/stations_projected.csv",
    "data/edi/qa/station_topography.png",
    "data/edi/qa/period_coverage.png",
    "data/edi/mt2femtic_data_manifest.json",
    "comparison/source_comparison.json",
    "comparison/source_comparison.csv",
    "comparison/station_coordinate_comparison.csv",
    "comparison/validation_summary.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path) -> list[Path]:
    return sorted(
        (
            path for path in root.rglob("*")
            if path.is_file()
            and path.name != "SHA256SUMS.txt"
            and "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def write_checksums(root: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in _files(root)
    ]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")


def verify_checksums(root: Path) -> int:
    path = root / "SHA256SUMS.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Missing checksum manifest: {path}")
    listed: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or relative in listed:
            raise ValueError(f"Invalid checksum row {line_number}")
        item = root / relative
        if not item.is_file() or sha256_file(item) != digest:
            raise ValueError(f"Checksum mismatch: {relative}")
        listed[relative] = digest
    expected = {path.relative_to(root).as_posix() for path in _files(root)}
    if set(listed) != expected:
        raise ValueError("SHA256SUMS.txt does not cover every release file")
    return len(listed)


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _verify_manifest(root: Path, source: str, stations: int, frequencies: int) -> dict[str, object]:
    output = root / "data" / source
    manifest = _json(output / "mt2femtic_data_manifest.json")
    if manifest.get("command") != "data" or manifest.get("config_kind") != "data":
        raise ValueError(f"Invalid {source} data manifest identity")
    stages = manifest.get("stages")
    if not isinstance(stages, dict) or len(stages) != 5:
        raise ValueError(f"Invalid {source} stage inventory")
    failed = [
        name for name, stage in stages.items()
        if not isinstance(stage, dict) or stage.get("status") != "passed"
    ]
    if failed:
        raise ValueError(f"Incomplete {source} stages: {failed}")
    source_stage = stages["source"]
    femtic_stage = stages["femtic_input"]
    if source_stage["outputs"].get("station_count") != stations:
        raise ValueError(f"Unexpected {source} station count")
    if femtic_stage["outputs"].get("mt_station_count") != stations:
        raise ValueError(f"Unexpected {source} FEMTIC station count")
    selection = manifest.get("resolved_config", {}).get("selection", {})
    if len(selection.get("periods_s", [])) != frequencies:
        raise ValueError(f"Unexpected {source} target-frequency count")
    for input_path, expected in manifest.get("input_hashes", {}).items():
        item = Path(input_path)
        if not item.is_file() or sha256_file(item) != expected:
            raise ValueError(f"{source} input hash mismatch: {input_path}")
    for relative, expected in manifest.get("output_hashes", {}).items():
        item = output / relative
        if not item.is_file() or sha256_file(item) != expected:
            raise ValueError(f"{source} output hash mismatch: {relative}")
    return manifest


def _verify_finite_observe(path: Path) -> None:
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        for token in line.split():
            try:
                value = float(token)
            except ValueError:
                continue
            if not math.isfinite(value) or abs(value) >= 1.0e30:
                raise ValueError(f"Invalid numeric value at {path}:{line_number}")


def _csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def verify(
    root: Path,
    expected_stations: int,
    expected_frequencies: int,
    expected_matched_complex: int,
    expected_modem_sha256: str,
    example: Path,
) -> dict[str, object]:
    package = root.resolve()
    for relative in REQUIRED:
        item = package / relative
        if not item.is_file() or item.stat().st_size == 0:
            raise FileNotFoundError(f"Missing or empty release file: {relative}")
    example = example.resolve()
    edi_files = sorted((example / "input" / "edi").glob("*.edi"))
    if len(edi_files) != expected_stations:
        raise ValueError(f"Expected {expected_stations} EDI files; found {len(edi_files)}")
    modem_input = example / "input" / "modem" / "BH_31.dat"
    if sha256_file(modem_input) != expected_modem_sha256:
        raise ValueError("BH_31.dat SHA-256 mismatch")

    manifests = {
        source: _verify_manifest(package, source, expected_stations, expected_frequencies)
        for source in ("modem", "edi")
    }
    modem_selection = manifests["modem"]["resolved_config"]["selection"]
    edi_selection = manifests["edi"]["resolved_config"]["selection"]
    edi_source = manifests["edi"]["resolved_config"]["source"]
    if modem_selection.get("frequency_policy") != "exact":
        raise ValueError("ModEM frequency policy must be exact")
    if edi_selection.get("frequency_policy") != "log_linear":
        raise ValueError("EDI frequency policy must be log_linear")
    if not edi_source.get("allow_time_convention_override"):
        raise ValueError("EDI time-convention override must be explicit")

    summary = _json(package / "comparison" / "validation_summary.json")
    identity = summary.get("station_identity", {})
    frequencies = summary.get("frequencies", {})
    if identity.get("matched_count") != expected_stations:
        raise ValueError("Station identity comparison is incomplete")
    if identity.get("missing_in_edi") or identity.get("extra_in_edi"):
        raise ValueError("Station identity comparison contains unmatched stations")
    if frequencies.get("target_count") != expected_frequencies:
        raise ValueError("Frequency comparison count is incorrect")
    if summary.get("matched_complex_count") != expected_matched_complex:
        raise ValueError("Response comparison pair count is incorrect")
    if summary.get("missing_in_edi_count") or summary.get("extra_in_edi_count"):
        raise ValueError("Response comparison contains unmatched records")
    if _csv_rows(package / "comparison" / "source_comparison.csv") != expected_matched_complex:
        raise ValueError("Response comparison CSV row count is incorrect")
    if _csv_rows(package / "comparison" / "station_coordinate_comparison.csv") != expected_stations:
        raise ValueError("Coordinate comparison CSV row count is incorrect")
    for source in ("modem", "edi"):
        _verify_finite_observe(package / "data" / source / "inversion_input" / "observe.dat")
    baseline = _json(example / "expected.json")["data_outputs"]
    for source in ("modem", "edi"):
        for name in ("observe.dat", "obs_site.dat", "distortion_iter0.dat"):
            item = package / "data" / source / "inversion_input" / name
            if sha256_file(item) != baseline[source][name]:
                raise ValueError(f"Baseline mismatch: {source}/{name}")
    checksum_count = verify_checksums(package)
    return {
        "status": "PASS",
        "modem_data_manifest": "PASS",
        "edi_data_manifest": "PASS",
        "file_hashes": "PASS",
        "data_baseline": "PASS",
        "station_identity": "PASS",
        "frequency_count": "PASS",
        "comparison_tables": "PASS",
        "station_count": expected_stations,
        "matched_complex_count": expected_matched_complex,
        "checksummed_file_count": checksum_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--example", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write-checksums", action="store_true")
    parser.add_argument("--expected-stations", type=int, default=21)
    parser.add_argument("--expected-frequencies", type=int, default=24)
    parser.add_argument("--expected-matched-complex", type=int, default=3006)
    parser.add_argument("--expected-modem-sha256", default=EXPECTED_MODEM_SHA256)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.write_checksums:
        write_checksums(root)
    print(json.dumps(verify(
        root,
        args.expected_stations,
        args.expected_frequencies,
        args.expected_matched_complex,
        args.expected_modem_sha256,
        args.example,
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
