#!/usr/bin/env python3
"""Prepare station-centered local refinement inputs for makeDHexaMesh."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Station:
    station_id: int
    x_km: float
    y_km: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _required_nonempty_line(stream, label: str) -> str:
    for line in stream:
        if line.strip():
            return line.strip()
    raise ValueError(f"Unexpected end of observe.dat while reading {label}")


def read_mt_stations(
    path: Path, *, expected_stations: int | None = None
) -> tuple[Station, ...]:
    """Read MT station IDs and horizontal coordinates from FEMTIC observe.dat."""

    path = Path(path)
    with path.open("r", encoding="ascii", errors="strict") as stream:
        section = _required_nonempty_line(stream, "MT section header").split()
        if len(section) != 2 or section[0] != "MT":
            raise ValueError("observe.dat must begin with an MT section")
        try:
            station_count = int(section[1])
        except ValueError as error:
            raise ValueError("Invalid MT station count") from error
        if station_count <= 0:
            raise ValueError("MT station count must be positive")

        stations: list[Station] = []
        station_ids: set[int] = set()
        for station_index in range(station_count):
            header = _required_nonempty_line(
                stream, f"MT station header {station_index}"
            ).split()
            if len(header) != 5:
                raise ValueError(f"MT station header {station_index} must have five fields")
            try:
                station_id = int(header[0])
                x_km = float(header[3])
                y_km = float(header[4])
            except ValueError as error:
                raise ValueError(f"Invalid MT station header {station_index}") from error
            if station_id in station_ids:
                raise ValueError(f"Duplicate MT station ID: {station_id}")
            if not math.isfinite(x_km) or not math.isfinite(y_km):
                raise ValueError(f"Non-finite MT station coordinate: {station_id}")
            station_ids.add(station_id)
            stations.append(Station(station_id=station_id, x_km=x_km, y_km=y_km))

            frequency_line = _required_nonempty_line(
                stream, f"frequency count for MT station {station_id}"
            ).split()
            if len(frequency_line) != 1:
                raise ValueError(f"Invalid frequency count for MT station {station_id}")
            try:
                frequency_count = int(frequency_line[0])
            except ValueError as error:
                raise ValueError(
                    f"Invalid frequency count for MT station {station_id}"
                ) from error
            if frequency_count <= 0:
                raise ValueError(
                    f"Frequency count must be positive for MT station {station_id}"
                )
            for frequency_index in range(frequency_count):
                _required_nonempty_line(
                    stream,
                    f"frequency row {frequency_index} for MT station {station_id}",
                )

    if expected_stations is not None and station_count != expected_stations:
        raise ValueError(
            f"Expected {expected_stations} MT stations, found {station_count}"
        )
    return tuple(stations)


def render_obs_site(
    stations: Sequence[Station],
    half_width_km: float,
    max_edge_km: float,
    inner_half_width_km: float | None = None,
    inner_max_edge_km: float | None = None,
) -> str:
    """Render one or two nested cuboid regions around each station."""

    if not stations:
        raise ValueError("At least one station is required")
    if not math.isfinite(half_width_km) or half_width_km <= 0.0:
        raise ValueError("half_width_km must be finite and positive")
    if not math.isfinite(max_edge_km) or max_edge_km <= 0.0:
        raise ValueError("max_edge_km must be finite and positive")
    if (inner_half_width_km is None) != (inner_max_edge_km is None):
        raise ValueError("Both inner-region values must be provided together")
    regions = [(half_width_km, max_edge_km)]
    if inner_half_width_km is not None and inner_max_edge_km is not None:
        if (
            not math.isfinite(inner_half_width_km)
            or inner_half_width_km <= 0.0
            or inner_half_width_km >= half_width_km
        ):
            raise ValueError(
                "inner_half_width_km must be finite, positive, and smaller than half_width_km"
            )
        if (
            not math.isfinite(inner_max_edge_km)
            or inner_max_edge_km <= 0.0
            or inner_max_edge_km >= max_edge_km
        ):
            raise ValueError(
                "inner_max_edge_km must be finite, positive, and smaller than max_edge_km"
            )
        regions = [
            (inner_half_width_km, inner_max_edge_km),
            (half_width_km, max_edge_km),
        ]
    lines = [str(len(stations))]
    for station in stations:
        lines.extend(
            (
                f"{station.x_km:.9f} {station.y_km:.9f} 0.000000000",
                str(len(regions)),
            )
        )
        for region_half_width_km, region_max_edge_km in regions:
            lines.append(
                f"{2.0 * region_half_width_km:.6f} "
                f"{region_max_edge_km:.6f} 0.000000"
            )
    lines.append("0")
    return "\n".join(lines) + "\n"


def render_refined_meshgen(base_text: str, parameter_level_limit: int) -> str:
    """Set horizontal partitioning and the parameter-cell refinement limit."""

    if parameter_level_limit < 0:
        raise ValueError("parameter_level_limit must be non-negative")
    lines = base_text.splitlines()
    type_indices = [index for index, line in enumerate(lines) if line.strip() == "TYPE"]
    if len(type_indices) != 1 or type_indices[0] + 1 >= len(lines):
        raise ValueError("Base meshgen must contain exactly one TYPE value")
    type_index = type_indices[0]
    if lines[type_index + 1].strip() != "1":
        raise ValueError("M5 requires TYPE=1 horizontal partitioning")
    if sum(line.strip() == "END" for line in lines) != 1:
        raise ValueError("Base meshgen must contain exactly one END marker")

    level_indices = [
        index for index, line in enumerate(lines) if line.strip() == "LEVEL_LIMIT_PARAM_CELL"
    ]
    if len(level_indices) > 1:
        raise ValueError("Base meshgen contains duplicate LEVEL_LIMIT_PARAM_CELL keywords")
    if level_indices:
        level_index = level_indices[0]
        if level_index + 1 >= len(lines):
            raise ValueError("LEVEL_LIMIT_PARAM_CELL is missing its value")
        lines[level_index + 1] = str(parameter_level_limit)
    else:
        lines[type_index:type_index] = [
            "LEVEL_LIMIT_PARAM_CELL",
            str(parameter_level_limit),
        ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe", required=True, type=Path)
    parser.add_argument("--base-meshgen", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--half-width-km", required=True, type=float)
    parser.add_argument("--max-edge-km", required=True, type=float)
    parser.add_argument("--inner-half-width-km", type=float)
    parser.add_argument("--inner-max-edge-km", type=float)
    parser.add_argument("--parameter-level-limit", required=True, type=int)
    parser.add_argument("--expected-stations", required=True, type=int)
    args = parser.parse_args(argv)

    stations = read_mt_stations(
        args.observe, expected_stations=args.expected_stations
    )
    meshgen_text = render_refined_meshgen(
        args.base_meshgen.read_text(encoding="ascii"),
        args.parameter_level_limit,
    )
    obs_site_text = render_obs_site(
        stations,
        half_width_km=args.half_width_km,
        max_edge_km=args.max_edge_km,
        inner_half_width_km=args.inner_half_width_km,
        inner_max_edge_km=args.inner_max_edge_km,
    )

    args.output_dir.mkdir(parents=True, exist_ok=False)
    meshgen_path = args.output_dir / "meshgen.inp"
    obs_site_path = args.output_dir / "obs_site.dat"
    meshgen_path.write_text(meshgen_text, encoding="ascii", newline="\n")
    obs_site_path.write_text(obs_site_text, encoding="ascii", newline="\n")
    summary = {
        "base_meshgen": str(args.base_meshgen.resolve()),
        "base_meshgen_sha256": _sha256(args.base_meshgen),
        "cuboid_side_length_km": 2.0 * args.half_width_km,
        "expected_stations": args.expected_stations,
        "half_width_km": args.half_width_km,
        "max_edge_km": args.max_edge_km,
        "inner_half_width_km": args.inner_half_width_km,
        "inner_max_edge_km": args.inner_max_edge_km,
        "meshgen_sha256": _sha256(meshgen_path),
        "observe": str(args.observe.resolve()),
        "observe_sha256": _sha256(args.observe),
        "obs_site_sha256": _sha256(obs_site_path),
        "parameter_level_limit": args.parameter_level_limit,
        "partitioning_type": 1,
        "station_count": len(stations),
        "stations": [asdict(station) for station in stations],
    }
    summary_path = args.output_dir / "preparation_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
