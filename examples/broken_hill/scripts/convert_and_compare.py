#!/usr/bin/env python3
"""Compare completed ModEM and EDI MT2FEMTIC data packages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Iterable, Sequence


COMPONENTS = ("ZXX", "ZXY", "ZYX", "ZYY", "TX", "TY")


def canonical_station_key(name: str, *, mode: str = "broken_hill") -> str:
    if mode == "exact":
        key = name.strip()
        if not key:
            raise ValueError("Cannot use an empty exact station key")
        return key
    if mode != "broken_hill":
        raise ValueError(f"Unknown station key mode: {mode}")
    match = re.search(r"BH[_-]?(\d+)", name, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Cannot identify Broken Hill station: {name}")
    return f"BH_{int(match.group(1))}"


def _nearest_rank(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize_differences(
    pairs: Iterable[tuple[complex, complex]],
) -> dict[str, float | int | None]:
    values = tuple(pairs)
    if not values:
        return {
            "count": 0,
            "median_absolute_difference": None,
            "p95_absolute_difference": None,
            "maximum_absolute_difference": None,
            "median_symmetric_relative_difference": None,
            "p95_symmetric_relative_difference": None,
            "maximum_symmetric_relative_difference": None,
        }
    absolute = [abs(left - right) for left, right in values]
    relative = [
        difference / max(abs(left), abs(right), 1.0e-30)
        for difference, (left, right) in zip(absolute, values)
    ]
    return {
        "count": len(values),
        "median_absolute_difference": statistics.median(absolute),
        "p95_absolute_difference": _nearest_rank(absolute, 0.95),
        "maximum_absolute_difference": max(absolute),
        "median_symmetric_relative_difference": statistics.median(relative),
        "p95_symmetric_relative_difference": _nearest_rank(relative, 0.95),
        "maximum_symmetric_relative_difference": max(relative),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _verified_manifest(output: Path) -> dict[str, object]:
    path = output / "mt2femtic_data_manifest.json"
    manifest = _load_json(path)
    if manifest.get("command") != "data" or manifest.get("config_kind") != "data":
        raise ValueError(f"Not an MT2FEMTIC data package: {output}")
    stages = manifest.get("stages")
    if not isinstance(stages, dict) or any(
        not isinstance(stage, dict) or stage.get("status") != "passed"
        for stage in stages.values()
    ):
        raise ValueError(f"Data manifest contains an incomplete gate: {path}")
    for source, expected in manifest.get("input_hashes", {}).items():
        item = Path(source)
        if not item.is_file() or _sha256(item) != expected:
            raise ValueError(f"Input hash mismatch: {source}")
    for relative, expected in manifest.get("output_hashes", {}).items():
        item = output / relative
        if not item.is_file() or _sha256(item) != expected:
            raise ValueError(f"Output hash mismatch: {item}")
    return manifest


def _station_table(
    output: Path,
    station_key_mode: str,
) -> dict[int, dict[str, object]]:
    path = output / "projection" / "stations_projected.csv"
    stations: dict[int, dict[str, object]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            stations[int(row["station_id"])] = {
                "name": canonical_station_key(
                    row["station_name"],
                    mode=station_key_mode,
                ),
                "x_km": float(row["model_x_km"]),
                "y_km": float(row["model_y_km"]),
            }
    if not stations:
        raise ValueError(f"No projected stations in {path}")
    return stations


def _frequency_key(value: float) -> str:
    return f"{value:.12g}"


def _observe_records(
    output: Path, stations: dict[int, dict[str, object]]
) -> dict[tuple[str, str, str], tuple[complex, float]]:
    path = output / "inversion_input" / "observe.dat"
    lines = [
        line.strip()
        for line in path.read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    cursor = 0
    records: dict[tuple[str, str, str], tuple[complex, float]] = {}
    for section, components, error_offset in (
        ("MT", COMPONENTS[:4], 9),
        ("VTF", COMPONENTS[4:], 5),
    ):
        header = lines[cursor].split()
        cursor += 1
        if len(header) != 2 or header[0] != section:
            raise ValueError(f"Expected {section} section in {path}")
        for _ in range(int(header[1])):
            station_id = int(lines[cursor].split()[0])
            cursor += 1
            if station_id not in stations:
                station_id -= 1000
            if station_id not in stations:
                raise ValueError(f"Unknown station ID in {path}: {station_id}")
            name = str(stations[station_id]["name"])
            sample_count = int(lines[cursor])
            cursor += 1
            for _ in range(sample_count):
                row = [float(token) for token in lines[cursor].split()]
                cursor += 1
                for index, component in enumerate(components):
                    error_real = row[error_offset + 2 * index]
                    error_imag = row[error_offset + 2 * index + 1]
                    if error_real < 0.0 and error_imag < 0.0:
                        continue
                    if error_real != error_imag:
                        raise ValueError(f"Unequal FEMTIC errors for {component}")
                    key = (name, _frequency_key(row[0]), component)
                    if key in records:
                        raise ValueError(f"Duplicate FEMTIC response: {key}")
                    records[key] = (
                        complex(row[1 + 2 * index], row[2 + 2 * index]),
                        error_real,
                    )
    if cursor != len(lines) - 1 or lines[cursor] != "END":
        raise ValueError(f"Invalid END marker in {path}")
    return records


def _write_csv(path: Path, rows: list[dict[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def compare(
    modem_output: Path,
    edi_output: Path,
    output: Path,
    *,
    station_key_mode: str = "broken_hill",
    require_complete_match: bool = False,
) -> dict[str, object]:
    destination = output.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Comparison output must be fresh: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    source_outputs = {"modem": modem_output.resolve(), "edi": edi_output.resolve()}
    manifests = {name: _verified_manifest(path) for name, path in source_outputs.items()}
    stations = {
        name: _station_table(path, station_key_mode)
        for name, path in source_outputs.items()
    }
    records = {
        name: _observe_records(source_outputs[name], stations[name])
        for name in source_outputs
    }

    modem_keys, edi_keys = set(records["modem"]), set(records["edi"])
    common = sorted(modem_keys & edi_keys)
    response_pairs = {component: [] for component in COMPONENTS}
    error_pairs = {component: [] for component in COMPONENTS}
    rows: list[dict[str, object]] = []
    for station, frequency, component in common:
        modem_value, modem_error = records["modem"][(station, frequency, component)]
        edi_value, edi_error = records["edi"][(station, frequency, component)]
        response_pairs[component].append((edi_value, modem_value))
        error_pairs[component].append((complex(edi_error), complex(modem_error)))
        absolute = abs(edi_value - modem_value)
        error_absolute = abs(edi_error - modem_error)
        rows.append({
            "station": station, "frequency_hz": frequency, "component": component,
            "modem_real": modem_value.real, "modem_imag": modem_value.imag,
            "edi_real": edi_value.real, "edi_imag": edi_value.imag,
            "absolute_difference": absolute,
            "symmetric_relative_difference": absolute / max(abs(edi_value), abs(modem_value), 1.0e-30),
            "modem_standard_error": modem_error, "edi_standard_error": edi_error,
            "error_absolute_difference": error_absolute,
            "error_symmetric_relative_difference": error_absolute / max(abs(edi_error), abs(modem_error), 1.0e-30),
        })
    fields = (
        "station", "frequency_hz", "component", "modem_real", "modem_imag",
        "edi_real", "edi_imag", "absolute_difference", "symmetric_relative_difference",
        "modem_standard_error", "edi_standard_error", "error_absolute_difference",
        "error_symmetric_relative_difference",
    )
    _write_csv(destination / "source_comparison.csv", rows, fields)

    station_sets = {
        name: {str(row["name"]): row for row in table.values()}
        for name, table in stations.items()
    }
    common_stations = sorted(set(station_sets["modem"]) & set(station_sets["edi"]))
    coordinate_rows: list[dict[str, object]] = []
    distances: list[float] = []
    for name in common_stations:
        modem_station, edi_station = station_sets["modem"][name], station_sets["edi"][name]
        dx = (float(edi_station["x_km"]) - float(modem_station["x_km"])) * 1000.0
        dy = (float(edi_station["y_km"]) - float(modem_station["y_km"])) * 1000.0
        distance = math.hypot(dx, dy)
        distances.append(distance)
        coordinate_rows.append({
            "station": name,
            "modem_x_north_km": modem_station["x_km"],
            "modem_y_east_km": modem_station["y_km"],
            "edi_x_north_km": edi_station["x_km"],
            "edi_y_east_km": edi_station["y_km"],
            "delta_x_m": dx, "delta_y_m": dy,
            "horizontal_difference_m": distance,
        })
    coordinate_fields = (
        "station", "modem_x_north_km", "modem_y_east_km", "edi_x_north_km",
        "edi_y_east_km", "delta_x_m", "delta_y_m", "horizontal_difference_m",
    )
    _write_csv(destination / "station_coordinate_comparison.csv", coordinate_rows, coordinate_fields)

    station_identity = {
        "modem_count": len(station_sets["modem"]),
        "edi_count": len(station_sets["edi"]),
        "matched_count": len(common_stations),
        "missing_in_edi": sorted(set(station_sets["modem"]) - set(station_sets["edi"])),
        "extra_in_edi": sorted(set(station_sets["edi"]) - set(station_sets["modem"])),
    }
    frequencies = {
        "target_count": len({key[1] for key in modem_keys}),
        "modem_count": len({key[1] for key in modem_keys}),
        "edi_count": len({key[1] for key in edi_keys}),
    }
    source_summary = {
        name: {
            "dataset_id": manifests[name].get("dataset_id"),
            "source_hashes": manifests[name].get("input_hashes", {}),
            "station_count": len(station_sets[name]),
            "frequency_count": len({key[1] for key in records[name]}),
            "active_complex_count": len(records[name]),
        }
        for name in ("modem", "edi")
    }
    comparison = {
        "matched_complex_count": len(common),
        "missing_in_edi_count": len(modem_keys - edi_keys),
        "extra_in_edi_count": len(edi_keys - modem_keys),
        "missing_in_edi_first_five": [list(key) for key in sorted(modem_keys - edi_keys)[:5]],
        "extra_in_edi_first_five": [list(key) for key in sorted(edi_keys - modem_keys)[:5]],
        "response_differences_by_component": {
            component: summarize_differences(response_pairs[component])
            for component in COMPONENTS
        },
        "standard_error_differences_by_component": {
            component: summarize_differences(error_pairs[component])
            for component in COMPONENTS
        },
        "coordinate_horizontal_difference_m": {
            "count": len(distances),
            "median": statistics.median(distances) if distances else None,
            "maximum": max(distances) if distances else None,
        },
        "interpretation": (
            "Each manifest and its hashes establish conversion integrity. "
            "Cross-source differences describe distinctions between the EDI and ModEM products."
        ),
    }
    summary = {**source_summary, "station_identity": station_identity, "frequencies": frequencies, **comparison}
    complete_match = not any(
        (
            station_identity["missing_in_edi"],
            station_identity["extra_in_edi"],
            comparison["missing_in_edi_count"],
            comparison["extra_in_edi_count"],
        )
    )
    summary["station_key_mode"] = station_key_mode
    summary["complete_match"] = complete_match
    (destination / "source_comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (destination / "validation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if require_complete_match and not complete_match:
        raise ValueError("EDI and ModEM routes do not have a complete station/response match")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modem-output", required=True, type=Path)
    parser.add_argument("--edi-output", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--station-key-mode",
        choices=("broken_hill", "exact"),
        default="broken_hill",
    )
    parser.add_argument("--require-complete-match", action="store_true")
    args = parser.parse_args()
    result = compare(
        args.modem_output,
        args.edi_output,
        args.output,
        station_key_mode=args.station_key_mode,
        require_complete_match=args.require_complete_match,
    )
    print(json.dumps({
        "status": "PASS",
        "matched_stations": result["station_identity"]["matched_count"],
        "matched_complex": result["matched_complex_count"],
        "output": str(args.output.resolve()),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
