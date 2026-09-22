"""Read and validate complete FEMTIC observation-side input sets.

The reader accepts both MT2FEMTIC data packages and existing FEMTIC data
directories.  It is intentionally read-only: provenance records belong to the
new mesh output directory, never to the supplied data directory.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .manifest import sha256_file
from .model import ResponseSample


_REQUIRED_FILENAMES = {
    "observe": "observe.dat",
    "obs_site": "obs_site.dat",
    "distortion": "distortion_iter0.dat",
}
_INTEGER = re.compile(r"[+-]?\d+\Z")


@dataclass(frozen=True)
class FemticStation:
    """One parsed station header and its frequency inventory."""

    section: str
    station_id: int
    magnetic_station_id: int
    selectors: tuple[int, ...]
    model_x_km: float
    model_y_km: float
    frequencies_hz: tuple[float, ...]
    samples: tuple[ResponseSample, ...] = ()


@dataclass(frozen=True)
class FemticInputSet:
    """Resolved, validated FEMTIC inputs consumed by the mesh stage."""

    layout: str
    paths: Mapping[str, Path]
    stations: tuple[FemticStation, ...]
    source_hashes: Mapping[str, str]
    topography_path: Path | None
    audit: Mapping[str, object]


def _read_nonempty(path: Path) -> list[str]:
    try:
        return [
            line.strip()
            for line in Path(path).read_text(encoding="ascii").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError as exc:
        raise ValueError(f"FEMTIC input is not ASCII: {path}") from exc


def _integer(token: str, label: str, *, minimum: int = 0) -> int:
    if not _INTEGER.fullmatch(token):
        raise ValueError(f"{label} must be an integer; found {token!r}")
    value = int(token)
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}; found {value}")
    return value


def _finite_row(line: str, expected_columns: int, label: str) -> list[float]:
    tokens = line.split()
    if len(tokens) != expected_columns:
        raise ValueError(
            f"{label} must contain {expected_columns} columns; found {len(tokens)}"
        )
    try:
        values = [float(token) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"{label} contains a non-numeric value") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} contains a non-finite value")
    return values


def _line(lines: list[str], cursor: int, label: str) -> str:
    if cursor >= len(lines):
        raise ValueError(f"Unexpected end of {label}")
    return lines[cursor]


def _parse_station_header(
    line: str,
    section: str,
    station_index: int,
) -> tuple[int, int, tuple[int, ...], float, float, int]:
    tokens = line.split()
    allowed = (4, 5, 6) if section == "MT" else (4, 5)
    if len(tokens) not in allowed:
        choices = ", ".join(str(value) for value in allowed)
        raise ValueError(
            f"{section} station {station_index} header must contain {choices} "
            f"columns; found {len(tokens)}"
        )
    station_id = _integer(
        tokens[0], f"{section} station {station_index} ID", minimum=1
    )
    magnetic_station_id = _integer(
        tokens[1], f"{section} station {station_index} magnetic ID", minimum=1
    )
    selectors = tuple(
        _integer(token, f"{section} station {station_index} selector")
        for token in tokens[2:-2]
    )
    if selectors and selectors[0] not in {0, 1}:
        raise ValueError(
            f"{section} station {station_index} owner-element selector "
            f"must be 0 or 1; found {selectors[0]}"
        )
    if section == "MT" and len(selectors) == 2 and selectors[1] not in {0, 1}:
        raise ValueError(
            f"MT station {station_index} electric-field selector "
            f"must be 0 or 1; found {selectors[1]}"
        )
    try:
        model_x_km, model_y_km = (float(tokens[-2]), float(tokens[-1]))
    except ValueError as exc:
        raise ValueError(
            f"{section} station {station_index} coordinates must be numeric"
        ) from exc
    if not math.isfinite(model_x_km) or not math.isfinite(model_y_km):
        raise ValueError(
            f"{section} station {station_index} coordinates must be finite"
        )
    return (
        station_id,
        magnetic_station_id,
        selectors,
        model_x_km,
        model_y_km,
        len(tokens),
    )


def _parse_section(
    lines: list[str],
    cursor: int,
    section: str,
) -> tuple[list[FemticStation], int, int, list[int]]:
    header = _line(lines, cursor, "observe.dat").split()
    if len(header) != 2 or header[0] != section:
        raise ValueError(f"observe.dat has no {section} header at the expected position")
    station_count = _integer(header[1], f"{section} station count", minimum=0)
    cursor += 1
    stations: list[FemticStation] = []
    header_columns: list[int] = []
    row_columns = 17 if section == "MT" else 9
    error_positions = (9, 11, 13, 15) if section == "MT" else (5, 7)
    for station_index in range(1, station_count + 1):
        parsed = _parse_station_header(
            _line(lines, cursor, "observe.dat"), section, station_index
        )
        cursor += 1
        station_id, magnetic_id, selectors, model_x, model_y, columns = parsed
        header_columns.append(columns)
        sample_count = _integer(
            _line(lines, cursor, "observe.dat"),
            f"{section} station {station_index} sample count",
            minimum=0,
        )
        cursor += 1
        frequencies: list[float] = []
        samples: list[ResponseSample] = []
        for sample_index in range(1, sample_count + 1):
            row = _finite_row(
                _line(lines, cursor, "observe.dat"),
                row_columns,
                f"{section} station {station_index} sample {sample_index}",
            )
            cursor += 1
            if row[0] <= 0.0:
                raise ValueError(f"{section} frequency must be positive")
            if row[0] in frequencies:
                raise ValueError(
                    f"Duplicate {section} frequency at station {station_id}: {row[0]}"
                )
            for position in error_positions:
                if row[position] != row[position + 1]:
                    raise ValueError(
                        f"{section} real and imaginary errors must match"
                    )
            frequencies.append(row[0])
            components = ("ZXX", "ZXY", "ZYX", "ZYY") if section == "MT" else ("TX", "TY")
            active = {
                name: (complex(row[1 + 2 * index], row[2 + 2 * index]), row[position])
                for index, (name, position) in enumerate(zip(components, error_positions))
                if row[position] >= 0.0
            }
            samples.append(ResponseSample(
                row[0], {name: value for name, (value, _) in active.items()},
                {name: error for name, (_, error) in active.items()}, frozenset(active),
            ))
        stations.append(
            FemticStation(
                section=section,
                station_id=station_id,
                magnetic_station_id=magnetic_id,
                selectors=selectors,
                model_x_km=model_x,
                model_y_km=model_y,
                frequencies_hz=tuple(frequencies),
                samples=tuple(samples),
            )
        )
    return stations, cursor, sum(len(item.frequencies_hz) for item in stations), header_columns


def _check_unique_ids(stations: list[FemticStation], section: str) -> None:
    identifiers = [station.station_id for station in stations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Duplicate {section} station ID")


def _parse_observe(path: Path) -> tuple[list[FemticStation], list[FemticStation], dict[str, object]]:
    lines = _read_nonempty(path)
    if not lines:
        raise ValueError("observe.dat is empty")
    mt_stations, cursor, mt_samples, mt_columns = _parse_section(lines, 0, "MT")
    if not mt_stations:
        raise ValueError("observe.dat must contain at least one MT station")
    _check_unique_ids(mt_stations, "MT")

    vtf_stations: list[FemticStation] = []
    vtf_samples = 0
    vtf_columns: list[int] = []
    if cursor < len(lines) and lines[cursor].split()[0] == "VTF":
        vtf_stations, cursor, vtf_samples, vtf_columns = _parse_section(
            lines, cursor, "VTF"
        )
        _check_unique_ids(vtf_stations, "VTF")
    if cursor >= len(lines) or lines[cursor] != "END" or cursor != len(lines) - 1:
        raise ValueError("observe.dat must end with one END marker")

    audit: dict[str, object] = {
        "mt_station_count": len(mt_stations),
        "mt_sample_count": mt_samples,
        "vtf_station_count": len(vtf_stations),
        "vtf_sample_count": vtf_samples,
        "mt_header_column_counts": mt_columns,
        "vtf_header_column_counts": vtf_columns,
    }
    return mt_stations, vtf_stations, audit


def _parse_obs_site(
    path: Path,
    expected_station_count: int,
) -> tuple[list[tuple[float, float, float]], int]:
    lines = _read_nonempty(path)
    if not lines:
        raise ValueError("obs_site.dat is empty")
    site_count = _integer(lines[0], "Observation site count", minimum=0)
    if site_count != expected_station_count:
        raise ValueError("obs_site.dat station count is inconsistent")
    cursor = 1
    locations: list[tuple[float, float, float]] = []
    total_rule_count = 0
    for station_index in range(1, site_count + 1):
        location = _finite_row(
            _line(lines, cursor, "obs_site.dat"),
            3,
            f"Observation site {station_index}",
        )
        cursor += 1
        locations.append((location[0], location[1], location[2]))
        rule_count = _integer(
            _line(lines, cursor, "obs_site.dat"),
            f"Observation site {station_index} refinement-rule count",
            minimum=1,
        )
        cursor += 1
        total_rule_count += rule_count
        for rule_index in range(1, rule_count + 1):
            rule = _finite_row(
                _line(lines, cursor, "obs_site.dat"),
                3,
                f"Observation site {station_index} refinement rule {rule_index}",
            )
            cursor += 1
            if rule[0] <= 0.0 or rule[1] < 0.0 or not rule[1].is_integer() or rule[2] <= 0.0:
                raise ValueError("Observation refinement rule is invalid")
    if cursor >= len(lines) or lines[cursor] != "0" or cursor != len(lines) - 1:
        raise ValueError("obs_site.dat must end with one 0 marker")
    return locations, total_rule_count


def _parse_distortion(path: Path, mt_station_ids: set[int]) -> int:
    lines = _read_nonempty(path)
    if not lines:
        raise ValueError("distortion_iter0.dat is empty")
    count = _integer(lines[0], "Distortion station count", minimum=0)
    if count != len(mt_station_ids) or len(lines) != count + 1:
        raise ValueError("distortion_iter0.dat count is inconsistent")
    identifiers: list[int] = []
    for row_index, line in enumerate(lines[1:], start=1):
        row = _finite_row(line, 6, f"Distortion row {row_index}")
        identifier = _integer(line.split()[0], f"Distortion row {row_index} ID", minimum=1)
        identifiers.append(identifier)
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"Distortion row {row_index} is non-finite")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate distortion station ID")
    if set(identifiers) != mt_station_ids:
        raise ValueError("distortion_iter0.dat station IDs do not match MT station IDs")
    return count


def _coordinates_match(
    first: tuple[float, float],
    second: tuple[float, float],
    tolerance_km: float,
) -> bool:
    return (
        abs(first[0] - second[0]) <= tolerance_km
        and abs(first[1] - second[1]) <= tolerance_km
    )


def validate_femtic_files(
    paths: Mapping[str, Path],
    coordinate_tolerance_km: float = 1.0e-9,
) -> dict[str, object]:
    """Validate a resolved FEMTIC trio and return a structural audit."""

    if set(paths) != set(_REQUIRED_FILENAMES):
        raise ValueError(
            "FEMTIC path mapping must contain observe, obs_site, and distortion"
        )
    if coordinate_tolerance_km < 0.0 or not math.isfinite(coordinate_tolerance_km):
        raise ValueError("FEMTIC coordinate tolerance must be finite and nonnegative")
    mt_stations, vtf_stations, audit = _parse_observe(Path(paths["observe"]))
    locations, rule_count = _parse_obs_site(
        Path(paths["obs_site"]), len(mt_stations)
    )
    for station, location in zip(mt_stations, locations, strict=True):
        if not _coordinates_match(
            (station.model_x_km, station.model_y_km),
            (location[0], location[1]),
            coordinate_tolerance_km,
        ):
            raise ValueError(
                f"MT station {station.station_id} coordinates do not match obs_site.dat"
            )
    site_xy = [(location[0], location[1]) for location in locations]
    for station in vtf_stations:
        if not any(
            _coordinates_match(
                (station.model_x_km, station.model_y_km), location, coordinate_tolerance_km
            )
            for location in site_xy
        ):
            raise ValueError(
                f"VTF station {station.station_id} has no matching obs_site.dat location"
            )
    distortion_count = _parse_distortion(
        Path(paths["distortion"]), {station.station_id for station in mt_stations}
    )
    audit.update(
        {
            "distortion_station_count": distortion_count,
            "observation_refinement_rule_count": rule_count,
        }
    )
    return audit


def _resolve_layout(data_root: Path) -> tuple[str, dict[str, Path], Path | None]:
    root = Path(data_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"FEMTIC data directory does not exist: {root}")
    external = {key: root / name for key, name in _REQUIRED_FILENAMES.items()}
    native_root = root / "inversion_input"
    native = {key: native_root / name for key, name in _REQUIRED_FILENAMES.items()}
    external_count = sum(path.is_file() for path in external.values())
    native_count = sum(path.is_file() for path in native.values())
    if external_count == len(external) and native_count == len(native):
        raise ValueError(
            "FEMTIC data layout is ambiguous: both external and native input trios exist"
        )
    if external_count == len(external):
        return "external", external, root / "topography.dat"
    if native_count == len(native):
        return "native", native, root / "projection" / "topography.dat"
    if external_count or native_count:
        candidates = external if external_count else native
        missing = [path.name for path in candidates.values() if not path.is_file()]
        raise ValueError(f"Missing FEMTIC input file(s): {', '.join(missing)}")
    raise ValueError(
        "Missing FEMTIC input files: observe.dat, obs_site.dat, distortion_iter0.dat"
    )


def _verify_native_manifest(root: Path, paths: Mapping[str, Path]) -> None:
    manifest_path = root / "mt2femtic_data_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Native data manifest is missing: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Native data manifest is invalid: {manifest_path}") from exc
    output_hashes = payload.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise ValueError("Native data manifest has no output_hashes mapping")
    for path in paths.values():
        relative = path.relative_to(root).as_posix()
        expected = output_hashes.get(relative)
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise ValueError(f"Native data hash mismatch: {relative}")


def load_femtic_input_set(
    data_root: Path,
    coordinate_tolerance_km: float = 1.0e-9,
) -> FemticInputSet:
    """Resolve and validate a native package or external FEMTIC directory."""

    root = Path(data_root).expanduser().resolve()
    layout, paths, topography_candidate = _resolve_layout(root)
    if layout == "native":
        _verify_native_manifest(root, paths)
    audit = validate_femtic_files(paths, coordinate_tolerance_km)
    mt_stations, _, _ = _parse_observe(paths["observe"])
    source_hashes = {
        str(path.resolve()): sha256_file(path)
        for path in paths.values()
    }
    topography_path = (
        topography_candidate.resolve()
        if topography_candidate is not None and topography_candidate.is_file()
        else None
    )
    return FemticInputSet(
        layout=layout,
        paths=paths,
        stations=tuple(mt_stations),
        source_hashes=source_hashes,
        topography_path=topography_path,
        audit=audit,
    )
