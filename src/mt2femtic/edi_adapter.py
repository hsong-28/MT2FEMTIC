"""EDI source adapter for the normalized PrepareData3D survey."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path

from .config import SourceConfig
from .conventions import (
    TARGET_IMPEDANCE_UNIT,
    TARGET_TIME_CONVENTION,
    impedance_scale_to_ohm,
    normalize_complex,
)
from .manifest import sha256_file
from .model import ResponseSample, Station, Survey


FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?")
CONVENTION_RE = re.compile(
    r"(?:SIGNCONVENTION|processing\.sign_convention)\s*=\s*exp\(\s*([+-])\s*i",
    re.IGNORECASE,
)
STATION_NAME_RE = re.compile(
    r"^\s*STATION\s+NAME\s*:\s*(\S.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
PLACEHOLDER_NAMES = frozenset({"none", "null", "unknown", "n/a", "na"})
NATURAL_NAME_PART = re.compile(r"(\d+)")

IMPEDANCE_TAGS = {
    "ZXX": (">ZXXR", ">ZXXI", ">ZXX.VAR"),
    "ZXY": (">ZXYR", ">ZXYI", ">ZXY.VAR"),
    "ZYX": (">ZYXR", ">ZYXI", ">ZYX.VAR"),
    "ZYY": (">ZYYR", ">ZYYI", ">ZYY.VAR"),
}
VTF_TAGS = {
    "TX": (">TXR.EXP", ">TXI.EXP", ">TXVAR.EXP"),
    "TY": (">TYR.EXP", ">TYI.EXP", ">TYVAR.EXP"),
}


def _parse_floats(text: str) -> list[float]:
    return [
        float(token.replace("D", "E").replace("d", "e"))
        for token in FLOAT_RE.findall(text)
    ]


def _strip_comment(text: str) -> str:
    return text.split("//", 1)[0]


def _parse_dms(value: str) -> float:
    cleaned = value.strip().strip('"').strip("'").replace(" ", "")
    hemisphere = ""
    if cleaned and cleaned[-1].upper() in {"N", "S", "E", "W"}:
        hemisphere = cleaned[-1].upper()
        cleaned = cleaned[:-1]
    sign = -1.0 if cleaned.startswith("-") else 1.0
    unsigned = cleaned.lstrip("+-")
    parts = unsigned.split(":")
    if len(parts) >= 3:
        result = sign * (
            abs(float(parts[0]))
            + float(parts[1]) / 60.0
            + float(parts[2]) / 3600.0
        )
    else:
        result = float(cleaned)
    if hemisphere in {"S", "W"}:
        return -abs(result)
    if hemisphere in {"N", "E"}:
        return abs(result)
    return result


def _header_values(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith(">") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip().upper()] = _strip_comment(value).strip()
    return result


def _sections(lines: list[str]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    current: str | None = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        first = stripped.split()[0].upper()
        if first.startswith(">"):
            current = first
            result.setdefault(current, [])
            remainder = _strip_comment(stripped[len(stripped.split()[0]) :].strip())
            if remainder and "=" not in remainder:
                result[current].extend(_parse_floats(remainder))
            continue
        if current is not None:
            result[current].extend(_parse_floats(_strip_comment(stripped)))
    return result


def _detect_time_convention(text: str) -> str | None:
    conventions = {
        "exp_minus_iwt" if match.group(1) == "-" else "exp_plus_iwt"
        for match in CONVENTION_RE.finditer(text)
    }
    if len(conventions) > 1:
        raise ValueError("Conflicting EDI time convention metadata")
    return next(iter(conventions), None)


def _resolve_time_convention(
    declared: str | None,
    config: SourceConfig,
) -> tuple[str, bool]:
    if declared is None:
        if not config.allow_time_convention_override:
            raise ValueError("Missing EDI time convention metadata")
        return config.time_convention, True
    if declared != config.time_convention:
        if not config.allow_time_convention_override:
            raise ValueError(
                f"EDI time convention {declared} conflicts with configured "
                f"{config.time_convention}"
            )
        return config.time_convention, True
    return declared, False


def _station_name(text: str, header: dict[str, str], source_path: Path) -> str:
    data_id = header.get("DATAID", "").strip().strip('"').strip("'")
    if data_id and data_id.casefold() not in PLACEHOLDER_NAMES:
        return data_id
    info_match = STATION_NAME_RE.search(text)
    if info_match is not None:
        info_name = info_match.group(1).strip().strip('"').strip("'")
        if info_name and info_name.casefold() not in PLACEHOLDER_NAMES:
            return info_name
    return source_path.stem


def _is_empty_marker(value: float, marker: float | None) -> bool:
    return marker is not None and value == marker


def _exact_section(
    sections: dict[str, list[float]],
    tag: str,
    count: int,
    *,
    optional: bool,
) -> list[float] | None:
    values = sections.get(tag)
    if values is None:
        if optional:
            return None
        raise ValueError(f"Missing EDI section {tag}")
    if len(values) != count:
        raise ValueError(
            f"{tag.removeprefix('>')} contains {len(values)} values; expected {count}"
        )
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{tag} contains non-finite values")
    return values


def _component_arrays(
    sections: dict[str, list[float]],
    tags: tuple[str, str, str],
    count: int,
) -> tuple[list[float], list[float], list[float]] | None:
    real = _exact_section(sections, tags[0], count, optional=True)
    imag = _exact_section(sections, tags[1], count, optional=True)
    variance = _exact_section(sections, tags[2], count, optional=True)
    if real is None and imag is None and variance is None:
        return None
    if real is None or imag is None:
        raise ValueError(f"EDI component {tags[0][1:-1]} requires real and imaginary sections")
    if variance is None:
        variance = [0.0] * count
    if any(value < 0.0 for value in variance):
        raise ValueError(f"{tags[2]} contains negative variance")
    return real, imag, variance


def _read_edi(
    path: Path,
    config: SourceConfig,
) -> tuple[Station, str | None, str, bool]:
    source_path = Path(path)
    try:
        text = source_path.read_text(encoding="ascii", errors="strict")
    except UnicodeDecodeError:
        text = source_path.read_text(encoding="latin-1", errors="strict")
    lines = text.splitlines()
    header = _header_values(lines)
    sections = _sections(lines)

    declared_convention = _detect_time_convention(text)
    source_convention, used_override = _resolve_time_convention(
        declared_convention,
        config,
    )
    frequencies = _exact_section(
        sections,
        ">FREQ",
        int(float(header["NFREQ"])) if "NFREQ" in header else len(sections.get(">FREQ", [])),
        optional=False,
    )
    assert frequencies is not None
    if not frequencies or any(value <= 0.0 for value in frequencies):
        raise ValueError(f"EDI frequencies must be finite and positive: {source_path}")
    if len(set(frequencies)) != len(frequencies):
        raise ValueError(f"EDI frequencies contain duplicates: {source_path}")

    longitude_text = header.get("LONGITUDE", header.get("LONG", header.get("LON")))
    latitude_text = header.get("LATITUDE", header.get("LAT"))
    if longitude_text is None or latitude_text is None:
        raise ValueError(f"EDI station is missing latitude or longitude: {source_path}")
    longitude = _parse_dms(longitude_text)
    latitude = _parse_dms(latitude_text)
    elevation_values = _parse_floats(header.get("ELEVATION", header.get("ELEV", "")))
    if not elevation_values:
        raise ValueError(f"EDI station is missing elevation: {source_path}")
    elevation = elevation_values[0]
    if not all(math.isfinite(value) for value in (longitude, latitude, elevation)):
        raise ValueError(f"EDI station contains non-finite coordinates: {source_path}")

    impedance = {
        name: arrays
        for name, tags in IMPEDANCE_TAGS.items()
        if (arrays := _component_arrays(sections, tags, len(frequencies))) is not None
    }
    if not impedance:
        raise ValueError(f"EDI station contains no impedance components: {source_path}")
    vtf = {
        name: arrays
        for name, tags in VTF_TAGS.items()
        if (arrays := _component_arrays(sections, tags, len(frequencies))) is not None
    }
    scale = impedance_scale_to_ohm(config.impedance_unit)
    empty_values = _parse_floats(header.get("EMPTY", ""))
    empty_marker = empty_values[0] if empty_values else None
    samples: list[ResponseSample] = []
    for index, frequency in enumerate(frequencies):
        values: dict[str, complex] = {}
        errors: dict[str, float] = {}
        active: set[str] = set()
        for name, (real, imag, variance) in impedance.items():
            if any(
                _is_empty_marker(value, empty_marker)
                for value in (real[index], imag[index], variance[index])
            ):
                continue
            values[name] = normalize_complex(
                complex(real[index], imag[index]) * scale,
                source_convention,
            )
            errors[name] = math.sqrt(variance[index]) * scale
            active.add(name)
        for name, (real, imag, variance) in vtf.items():
            if any(
                _is_empty_marker(value, empty_marker)
                for value in (real[index], imag[index], variance[index])
            ):
                continue
            values[name] = normalize_complex(
                complex(real[index], imag[index]),
                source_convention,
            )
            errors[name] = math.sqrt(variance[index])
            active.add(name)
        samples.append(
            ResponseSample(
                frequency_hz=frequency,
                values=values,
                standard_errors=errors,
                active_components=frozenset(active),
            )
        )

    name = (
        source_path.stem
        if config.station_name_source == "file_stem"
        else _station_name(text, header, source_path)
    )
    if not name:
        raise ValueError(f"EDI station has an empty DATAID: {source_path}")
    station = Station(
        station_id=0,
        name=name,
        longitude_deg=longitude,
        latitude_deg=latitude,
        elevation_m=elevation,
        north_m=None,
        east_m=None,
        model_x_km=None,
        model_y_km=None,
        surface_depth_km=None,
        samples=tuple(samples),
    )
    return station, declared_convention, source_convention, used_override


def read_edi_file(path: Path, config: SourceConfig) -> Station:
    if config.type != "edi":
        raise ValueError("EDI adapter requires source.type=edi")
    return _read_edi(Path(path), config)[0]


def _natural_path_key(path: Path) -> tuple[tuple[int, object], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in NATURAL_NAME_PART.split(path.name)
        if part
    )


def resolve_edi_files(config: SourceConfig) -> tuple[Path, ...]:
    if config.type != "edi":
        raise ValueError("EDI adapter requires source.type=edi")
    if not config.path.is_dir():
        raise FileNotFoundError(f"EDI input directory does not exist: {config.path}")
    if config.edi_list_file is None:
        files = sorted(config.path.glob(config.edi_pattern), key=_natural_path_key)
    else:
        inventory = config.edi_list_file
        if not inventory.is_file():
            raise FileNotFoundError(f"EDI inventory does not exist: {inventory}")
        rows = [line.strip() for line in inventory.read_text(encoding="utf-8").splitlines()]
        if not rows:
            raise ValueError(f"EDI inventory is empty: {inventory}")
        try:
            declared = int(rows[0])
        except ValueError as exc:
            raise ValueError(f"Invalid EDI inventory count: {rows[0]!r}") from exc
        names = [row for row in rows[1:] if row]
        if declared != len(names):
            raise ValueError(
                f"EDI inventory declares {declared} files but lists {len(names)}"
            )
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("EDI inventory contains duplicate filenames")
        root = config.path.resolve()
        files = []
        for name in names:
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise ValueError(f"EDI inventory path escapes source directory: {name}")
            if not path.is_file():
                raise FileNotFoundError(f"EDI inventory file does not exist: {path}")
            files.append(path)
    if not files:
        raise FileNotFoundError(
            f"No EDI files match {config.edi_pattern!r} in {config.path}"
        )
    return tuple(files)


def read_edi_directory(config: SourceConfig) -> Survey:
    files = resolve_edi_files(config)

    stations: list[Station] = []
    names: set[str] = set()
    overrides: list[str] = []
    declared: dict[str, str | None] = {}
    hashes: dict[str, str] = {}
    if config.edi_list_file is not None:
        hashes[str(config.edi_list_file.resolve())] = sha256_file(config.edi_list_file)
    for station_id, path in enumerate(files, start=1):
        station, declared_convention, _source_convention, used_override = _read_edi(
            path,
            config,
        )
        normalized_name = station.name.casefold()
        if normalized_name in names:
            raise ValueError(f"Duplicate station name: {station.name}")
        names.add(normalized_name)
        station = replace(station, station_id=station_id)
        stations.append(station)
        declared[station.name] = declared_convention
        hashes[str(path.resolve())] = sha256_file(path)
        if used_override:
            overrides.append(station.name)

    return Survey(
        stations=tuple(stations),
        impedance_unit=TARGET_IMPEDANCE_UNIT,
        time_convention=TARGET_TIME_CONVENTION,
        source_type="edi",
        metadata={
            "input_hashes": hashes,
            "declared_time_conventions": declared,
            "time_convention_overrides": overrides,
            "source_impedance_unit": config.impedance_unit,
            "source_time_convention": config.time_convention,
            "edi_inventory": (
                config.edi_list_file.name if config.edi_list_file is not None else None
            ),
            "station_name_source": config.station_name_source,
        },
    )
