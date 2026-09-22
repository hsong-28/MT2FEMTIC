"""ModEM list-data and WS-model adapters."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Sequence

from .config import SourceConfig
from .conventions import (
    TARGET_IMPEDANCE_UNIT,
    TARGET_TIME_CONVENTION,
    impedance_scale_to_ohm,
    normalize_complex,
)
from .manifest import sha256_file
from .model import ModemModel, ResponseSample, Station, Survey


CONVENTION_RE = re.compile(r"exp\(\s*([+-])\s*i", re.IGNORECASE)
COMPONENT_NAMES = {
    "ZXX": "ZXX",
    "ZXY": "ZXY",
    "ZYX": "ZYX",
    "ZYY": "ZYY",
    "TZX": "TX",
    "TZY": "TY",
    "TX": "TX",
    "TY": "TY",
}


def _detect_header_convention(lines: Sequence[str]) -> str | None:
    found = {
        "exp_minus_iwt" if match.group(1) == "-" else "exp_plus_iwt"
        for line in lines
        if line.lstrip().startswith(("#", ">"))
        for match in CONVENTION_RE.finditer(line)
    }
    if len(found) > 1:
        raise ValueError("Conflicting ModEM time convention metadata")
    return next(iter(found), None)


def _resolve_convention(declared: str | None, config: SourceConfig) -> tuple[str, bool]:
    if declared is None:
        if not config.allow_time_convention_override:
            raise ValueError("Missing ModEM time convention metadata")
        return config.time_convention, True
    if declared != config.time_convention:
        if not config.allow_time_convention_override:
            raise ValueError(
                f"ModEM time convention {declared} conflicts with configured "
                f"{config.time_convention}"
            )
        return config.time_convention, True
    return declared, False


def read_modem_data(
    path: Path, config: SourceConfig, *, require_impedance: bool = True,
) -> Survey:
    if config.type != "modem":
        raise ValueError("ModEM adapter requires source.type=modem")
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"ModEM data file does not exist: {source_path}")
    lines = source_path.read_text(encoding="ascii", errors="strict").splitlines()
    declared_convention = _detect_header_convention(lines)
    source_convention, used_override = _resolve_convention(declared_convention, config)
    impedance_scale = impedance_scale_to_ohm(config.impedance_unit)

    station_order: list[str] = []
    coordinates: dict[str, tuple[float, float, float, float, float]] = {}
    period_order: dict[str, list[float]] = {}
    rows: dict[tuple[str, float], dict[str, tuple[complex, float]]] = {}
    for line_number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", ">")):
            continue
        tokens = stripped.split()
        if len(tokens) < 11:
            raise ValueError(
                f"Invalid ModEM data row at line {line_number}: expected 11 fields"
            )
        try:
            period_s = float(tokens[0])
            latitude_deg = float(tokens[2])
            longitude_deg = float(tokens[3])
            north_m = float(tokens[4])
            east_m = float(tokens[5])
            elevation_m = float(tokens[6])
            real_value = float(tokens[8])
            imag_value = float(tokens[9])
            standard_error = float(tokens[10])
        except ValueError as exc:
            raise ValueError(f"Invalid numeric ModEM field at line {line_number}") from exc
        numeric = (
            period_s,
            latitude_deg,
            longitude_deg,
            north_m,
            east_m,
            elevation_m,
            real_value,
            imag_value,
            standard_error,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError(f"Non-finite ModEM value at line {line_number}")
        if period_s <= 0.0:
            raise ValueError(f"ModEM period must be positive at line {line_number}")
        if standard_error < 0.0:
            raise ValueError(f"ModEM standard error must be nonnegative at line {line_number}")
        station_name = tokens[1]
        if not station_name:
            raise ValueError(f"Empty ModEM station name at line {line_number}")
        raw_component = tokens[7].upper()
        if raw_component not in COMPONENT_NAMES:
            raise ValueError(
                f"Unsupported ModEM component {raw_component} at line {line_number}"
            )
        component = COMPONENT_NAMES[raw_component]
        coordinate = (longitude_deg, latitude_deg, north_m, east_m, elevation_m)
        if station_name not in coordinates:
            station_order.append(station_name)
            coordinates[station_name] = coordinate
            period_order[station_name] = []
        elif coordinates[station_name] != coordinate:
            raise ValueError(f"Inconsistent coordinates for ModEM station {station_name}")
        key = (station_name, period_s)
        if period_s not in period_order[station_name]:
            period_order[station_name].append(period_s)
        component_rows = rows.setdefault(key, {})
        if component in component_rows:
            raise ValueError(
                f"Duplicate ModEM component {component} for {station_name} at {period_s:g} s"
            )
        scale = impedance_scale if component.startswith("Z") else 1.0
        component_rows[component] = (
            normalize_complex(complex(real_value, imag_value) * scale, source_convention),
            standard_error * scale,
        )

    if not station_order:
        raise ValueError(f"No ModEM data rows found in {source_path}")
    stations: list[Station] = []
    for station_id, station_name in enumerate(station_order, start=1):
        longitude_deg, latitude_deg, north_m, east_m, elevation_m = coordinates[station_name]
        samples: list[ResponseSample] = []
        for period_s in period_order[station_name]:
            components = rows[(station_name, period_s)]
            if require_impedance and not any(name.startswith("Z") for name in components):
                raise ValueError(
                    f"ModEM station {station_name} period {period_s:g} s has no impedance"
                )
            samples.append(
                ResponseSample(
                    frequency_hz=1.0 / period_s,
                    values={name: value for name, (value, _error) in components.items()},
                    standard_errors={name: error for name, (_value, error) in components.items()},
                    active_components=frozenset(components),
                )
            )
        stations.append(
            Station(
                station_id=station_id,
                name=station_name,
                longitude_deg=longitude_deg,
                latitude_deg=latitude_deg,
                elevation_m=elevation_m,
                north_m=north_m,
                east_m=east_m,
                model_x_km=None,
                model_y_km=None,
                surface_depth_km=None,
                samples=tuple(samples),
            )
        )
    return Survey(
        stations=tuple(stations),
        impedance_unit=TARGET_IMPEDANCE_UNIT,
        time_convention=TARGET_TIME_CONVENTION,
        source_type="modem",
        metadata={
            "input_hashes": {str(source_path.resolve()): sha256_file(source_path)},
            "declared_time_convention": declared_convention,
            "source_time_convention": source_convention,
            "time_convention_override": used_override,
            "source_impedance_unit": config.impedance_unit,
            "coordinate_mapping": "FEMTIC X=ModEM north; FEMTIC Y=ModEM east",
        },
    )


def _finite_positive(values: Sequence[float], label: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or any(not math.isfinite(value) or value <= 0.0 for value in result):
        raise ValueError(f"{label} must contain finite positive values")
    return result


def read_modem_model(path: Path) -> ModemModel:
    source_path = Path(path)
    lines = [
        line.strip()
        for line in source_path.read_text(encoding="ascii", errors="strict").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"Empty ModEM model: {source_path}")
    header = lines[0].split()
    if len(header) < 5:
        raise ValueError(f"Invalid ModEM model header: {source_path}")
    try:
        north_count, east_count, depth_count = (int(value) for value in header[:3])
    except ValueError as exc:
        raise ValueError(f"Invalid ModEM model dimensions: {source_path}") from exc
    if min(north_count, east_count, depth_count) <= 0:
        raise ValueError("ModEM model dimensions must be positive")
    representation = header[4].upper()
    if representation not in {"LOGE", "LOG10", "LINEAR"}:
        raise ValueError(f"Unsupported ModEM model representation: {representation}")
    try:
        numeric = [float(token) for line in lines[1:] for token in line.split()]
    except ValueError as exc:
        raise ValueError(f"Invalid numeric token in ModEM model: {source_path}") from exc
    width_count = north_count + east_count + depth_count
    model_count = north_count * east_count * depth_count
    expected_count = width_count + model_count + 4
    if len(numeric) != expected_count:
        raise ValueError(
            f"Unexpected numeric token count in {source_path}: "
            f"expected {expected_count}, found {len(numeric)}"
        )
    cursor = 0
    north_widths = _finite_positive(
        numeric[cursor : cursor + north_count],
        "north widths",
    )
    cursor += north_count
    east_widths = _finite_positive(
        numeric[cursor : cursor + east_count],
        "east widths",
    )
    cursor += east_count
    depth_widths = _finite_positive(
        numeric[cursor : cursor + depth_count],
        "depth widths",
    )
    cursor += depth_count
    encoded = numeric[cursor : cursor + model_count]
    cursor += model_count
    if not all(math.isfinite(value) for value in encoded):
        raise ValueError("ModEM model contains non-finite values")
    if representation == "LOGE":
        resistivity = tuple(math.exp(value) for value in encoded)
    elif representation == "LOG10":
        resistivity = tuple(10.0**value for value in encoded)
    else:
        resistivity = _finite_positive(encoded, "resistivity")
    if any(not math.isfinite(value) or value <= 0.0 for value in resistivity):
        raise ValueError("Decoded ModEM resistivity must be finite and positive")
    origin = tuple(numeric[cursor : cursor + 3])
    cursor += 3
    rotation = numeric[cursor]
    if len(origin) != 3 or not all(math.isfinite(value) for value in origin):
        raise ValueError("Invalid ModEM model origin")
    if not math.isfinite(rotation):
        raise ValueError("Invalid ModEM model rotation")
    return ModemModel(
        dimensions=(north_count, east_count, depth_count),
        north_widths_m=north_widths,
        east_widths_m=east_widths,
        depth_widths_m=depth_widths,
        resistivity_ohm_m=resistivity,
        origin_m=(origin[0], origin[1], origin[2]),
        rotation_deg=rotation,
        representation=representation,
    )


def modem_values_in_femtic_order(model: ModemModel) -> tuple[float, ...]:
    north_count, east_count, depth_count = model.dimensions
    expected_count = north_count * east_count * depth_count
    if len(model.resistivity_ohm_m) != expected_count:
        raise ValueError("ModEM model dimensions do not match resistivity count")
    result: list[float] = []
    layer_size = north_count * east_count
    for depth_index in range(depth_count):
        layer_offset = depth_index * layer_size
        for east_index in range(east_count):
            for femtic_north_index in range(north_count):
                modem_north_index = north_count - 1 - femtic_north_index
                result.append(
                    model.resistivity_ohm_m[
                        layer_offset
                        + east_index * north_count
                        + modem_north_index
                    ]
                )
    return tuple(result)
