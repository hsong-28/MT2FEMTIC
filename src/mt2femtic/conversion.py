"""Observation-only conversion in a shared, unrotated local coordinate frame."""

import json
import math
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from .conventions import FIELD_TO_OHM
from .data_config import SourceConfig
from .femtic_io import _parse_observe, load_femtic_input_set
from .femtic_writer import write_femtic_observe
from .jif3d_io import read_jif3d, write_jif3d
from .manifest import sha256_file
from .model import ResponseSample, Station, Survey
from .modem_adapter import _detect_header_convention, read_modem_data


FILENAMES = {"femtic": "observe.dat", "modem": "survey.dat", "jif3d": "survey.nc"}
STATION_FIELDS = ("station_id", "name", "longitude_deg", "latitude_deg", "surface_depth_km")


def _read_femtic(path: Path) -> Survey:
    mt, vtf, _ = _parse_observe(path)
    tips = {s.station_id: s for s in vtf}
    used = set()
    stations = []
    for item in mt:
        if len(item.selectors) == 2 and item.selectors[1] != 0:
            raise ValueError("Tangential FEMTIC electric fields are outside observation conversion")
        tip = tips.get(item.magnetic_station_id)
        if tip is not None:
            if (tip.magnetic_station_id != tip.station_id or
                (tip.model_x_km, tip.model_y_km) != (item.model_x_km, item.model_y_km)):
                raise ValueError("Only co-located FEMTIC MT/VTF stations are supported")
            if tip.station_id in used:
                raise ValueError("Shared FEMTIC magnetic stations are outside observation conversion")
            used.add(tip.station_id)
        rows = {}
        for sample in (*item.samples, *(tip.samples if tip else ())):
            values, errors = rows.setdefault(sample.frequency_hz, ({}, {}))
            values.update(sample.values)
            errors.update(sample.standard_errors)
        samples = tuple(ResponseSample(f, values, errors, frozenset(values))
                        for f, (values, errors) in rows.items() if values)
        stations.append(Station(item.station_id, str(item.station_id), None, None, None,
                                item.model_x_km * 1000, item.model_y_km * 1000,
                                item.model_x_km, item.model_y_km, None, samples))
    if any(s.station_id not in used and any(row.active_components for row in s.samples) for s in vtf):
        raise ValueError("Unpaired FEMTIC VTF station; no observations were discarded")
    return Survey(tuple(stations), "ohm", "exp_minus_iwt", "femtic")


def _read_modem(path: Path) -> Survey:
    lines = path.read_text(encoding="ascii").splitlines()
    headers = [line.lstrip()[1:].strip() for line in lines if line.lstrip().startswith(">")]
    if not headers or len(headers) % 6:
        raise ValueError("Conversion requires complete six-line ModEM block headers")
    units, origins = set(), set()
    for offset in range(0, len(headers), 6):
        kind, sign, unit, angle, origin, counts = headers[offset:offset + 6]
        if kind not in {"Full_Impedance", "Off_Diagonal_Impedance", "Full_Vertical_Components"}:
            raise ValueError(f"Unsupported ModEM data type: {kind}")
        if _detect_header_convention(["> " + sign]) is None:
            raise ValueError("Missing ModEM block time convention")
        if float(angle) != 0:
            raise ValueError("ModEM orientation must be zero; rotate the source explicitly first")
        position = tuple(float(v) for v in origin.split())
        if len(position) not in (2, 3) or not all(math.isfinite(v) for v in position):
            raise ValueError("Invalid ModEM origin")
        origins.add(position)
        if not re.fullmatch(r"\d+\s+\d+", counts):
            raise ValueError("Invalid ModEM period/station counts")
        if kind == "Full_Vertical_Components":
            if unit != "[]":
                raise ValueError("ModEM tipper units must be []")
        else:
            if unit.lower() not in {"[mv/km]/[nt]", "[ohm]", "ohm"}:
                raise ValueError(f"Unsupported ModEM impedance units: {unit}")
            units.add("mv_per_km_per_nt" if unit.lower() == "[mv/km]/[nt]" else "ohm")
    block = -1
    rows_by_block = {}
    header_index = 0
    for line in lines:
        if line.lstrip().startswith(">"):
            block = header_index // 6
            header_index += 1
        elif line.strip() and not line.lstrip().startswith("#"):
            tokens = line.split()
            if block < 0 or header_index % 6 or len(tokens) != 11:
                raise ValueError("ModEM rows must follow a complete block header and contain 11 columns")
            is_tipper = headers[block * 6] == "Full_Vertical_Components"
            allowed = ("TX", "TY", "TZX", "TZY") if is_tipper else ("ZXX", "ZXY", "ZYX", "ZYY")
            if headers[block * 6] == "Off_Diagonal_Impedance":
                allowed = ("ZXY", "ZYX")
            if tokens[7].upper() not in allowed:
                raise ValueError("ModEM component does not match its block type")
            periods, names = rows_by_block.setdefault(block, (set(), set()))
            periods.add(float(tokens[0]))
            names.add(tokens[1])
    for block in range(len(headers) // 6):
        periods, names = rows_by_block.get(block, (set(), set()))
        if tuple(map(int, headers[block * 6 + 5].split())) != (len(periods), len(names)):
            raise ValueError("ModEM period/station counts do not match the block rows")
    if len(units) > 1 or len(origins) != 1:
        raise ValueError("ModEM blocks must share their impedance units and coordinate origin")
    convention = _detect_header_convention(lines)
    config = SourceConfig("modem", path, next(iter(units), "ohm"), convention, False, "*.edi")
    survey = read_modem_data(path, config, require_impedance=False)
    # The legacy data adapter calls column 7 elevation; the standard ModEM field is Z down.
    stations = tuple(replace(s, model_x_km=s.north_m / 1000, model_y_km=s.east_m / 1000,
                             surface_depth_km=s.elevation_m / 1000, elevation_m=None)
                     for s in survey.stations)
    return replace(survey, stations=stations, metadata={"modem_origin": list(next(iter(origins)))})


def read_observations(source: Path, format: str, surface_depth_m: float | None = None) -> Survey:
    """Read responses without selection, interpolation, rotation, or error floors."""
    source = Path(source).resolve()
    if source.is_dir():
        if format == "femtic" and (source / "mt2femtic_data_manifest.json").is_file():
            manifest = json.loads((source / "mt2femtic_data_manifest.json").read_text())
            angle = manifest["resolved_config"]["coordinates"]["model_axis_azimuth_deg"]
            if angle != 0:
                raise ValueError("Native FEMTIC data must use zero model-axis azimuth for conversion")
            source = load_femtic_input_set(source).paths["observe"]
        else:
            source = source / FILENAMES[format]
    reader = {"femtic": _read_femtic, "modem": _read_modem, "jif3d": read_jif3d}[format]
    survey = reader(source)
    evidence = source.parent / "conversion.json"
    if evidence.is_file():
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format") != format or payload.get("output_sha256") != sha256_file(source):
            raise ValueError("Conversion metadata does not match the input file")
        metadata = payload.get("stations", [])
        if not isinstance(metadata, list) or len(metadata) != len(survey.stations):
            raise ValueError("Conversion station metadata count mismatch")
        lookup = {str(s.station_id) if format == "femtic" else s.name: s for s in survey.stations}
        restored = []
        for row in metadata:
            if not isinstance(row, dict) or not set(STATION_FIELDS).issubset(row):
                raise ValueError("Incomplete conversion station metadata")
            if not isinstance(row["name"], str) or not isinstance(row["station_id"], int):
                raise ValueError("Invalid conversion station identity")
            for key in ("longitude_deg", "latitude_deg", "surface_depth_km"):
                if row[key] is not None and (not isinstance(row[key], (int, float)) or not math.isfinite(row[key])):
                    raise ValueError(f"Invalid conversion station {key}")
            key = str(row["station_id"]) if format == "femtic" else row["name"]
            if key not in lookup:
                raise ValueError("Conversion station metadata does not match file station keys")
            station = lookup.pop(key)
            if station.surface_depth_km is not None and (row["surface_depth_km"] is None or not math.isclose(station.surface_depth_km, row["surface_depth_km"], rel_tol=2e-14, abs_tol=0)):
                raise ValueError("Conversion station depth differs from the data file")
            restored.append(replace(station, **{key: row[key] for key in STATION_FIELDS}))
        retained = payload.get("metadata", {})
        if not isinstance(retained, dict):
            raise ValueError("Invalid conversion metadata")
        survey = replace(survey, stations=tuple(restored), metadata=retained)
    if surface_depth_m is not None:
        if format != "femtic" or not math.isfinite(surface_depth_m):
            raise ValueError("--surface-depth-m is a finite, FEMTIC-only depth declaration")
        if any(s.surface_depth_km is not None and s.surface_depth_km * 1000 != surface_depth_m for s in survey.stations):
            raise ValueError("--surface-depth-m conflicts with retained station depths")
        survey = replace(survey, stations=tuple(replace(s, surface_depth_km=surface_depth_m / 1000) for s in survey.stations))
    _validate(survey)
    return replace(survey, metadata={**survey.metadata, "input_file": source.name, "input_sha256": sha256_file(source)})


def _validate(survey: Survey) -> None:
    if not survey.stations:
        raise ValueError("No observation stations")
    ids = [s.station_id for s in survey.stations]
    names = [s.name for s in survey.stations]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names) or any(not name or re.search(r"\s", name) or not name.isascii() for name in names):
        raise ValueError("Station IDs and ASCII names must be unique; names cannot contain whitespace")
    for station in survey.stations:
        if not isinstance(station.station_id, int) or station.station_id <= 0 or not station.samples:
            raise ValueError(f"Invalid or empty station: {station.name}")
        for value in (station.model_x_km, station.model_y_km, station.surface_depth_km, station.latitude_deg, station.longitude_deg):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"Non-finite coordinate: {station.name}")
        if station.latitude_deg is not None and not -90 <= station.latitude_deg <= 90:
            raise ValueError("Latitude outside [-90, 90]")
        if station.longitude_deg is not None and not -180 <= station.longitude_deg <= 180:
            raise ValueError("Longitude outside [-180, 180]")
        for sample in station.samples:
            if not math.isfinite(sample.frequency_hz) or sample.frequency_hz <= 0:
                raise ValueError("Frequency must be finite and positive")
            for component in sample.active_components:
                value, error = sample.values[component], sample.standard_errors[component]
                if not all(math.isfinite(v) for v in (value.real, value.imag, error)) or error < 0:
                    raise ValueError(f"Invalid observation: {station.name}, {component}")


def _write_modem(survey: Survey, path: Path) -> None:
    lines = ["# Observation conversion; local X north, Y east, Z down, in metres.",
             "# Unknown geographic coordinates are 0 placeholders; see conversion.json."]
    origin = " ".join(f"{v:.17g}" for v in survey.metadata.get("modem_origin", [0.0, 0.0]))
    for kind, components in (("Full_Impedance", ("ZXX", "ZXY", "ZYX", "ZYY")),
                             ("Full_Vertical_Components", ("TX", "TY"))):
        rows, frequencies, stations = [], set(), set()
        for s in survey.stations:
            for sample in s.samples:
                for c in components:
                    if c not in sample.active_components:
                        continue
                    scale = FIELD_TO_OHM if c.startswith("Z") else 1.0
                    value = sample.values[c].conjugate() / scale
                    error = sample.standard_errors[c] / scale
                    columns = [1 / sample.frequency_hz, s.name, s.latitude_deg or 0, s.longitude_deg or 0,
                               s.model_x_km * 1000, s.model_y_km * 1000, s.surface_depth_km * 1000,
                               c, value.real, value.imag, error]
                    rows.append(" ".join(v if isinstance(v, str) else f"{v:.17g}" for v in columns))
                    frequencies.add(sample.frequency_hz)
                    stations.add(s.name)
        if rows:
            unit = "[mV/km]/[nT]" if kind == "Full_Impedance" else "[]"
            lines.extend([f"> {kind}", r"> exp(+i\omega t)", f"> {unit}", "> 0", f"> {origin}",
                          f"> {len(frequencies)} {len(stations)}", *rows])
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def convert(source: Path, output: Path, source_format: str, target_format: str,
            surface_depth_m: float | None = None) -> dict:
    """Write a new output directory and retain otherwise unrepresentable station metadata."""
    target = Path(output).resolve()
    if target.exists():
        raise ValueError(f"Output already exists; choose a fresh directory: {target}")
    survey = read_observations(source, source_format, surface_depth_m)
    if target_format != "femtic" and any(s.surface_depth_km is None for s in survey.stations):
        raise ValueError("FEMTIC observe.dat has no depths; declare --surface-depth-m or retain conversion.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".conversion-", dir=target.parent) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        path = staging / FILENAMES[target_format]
        if target_format == "femtic":
            write_femtic_observe(survey, path, precision=17)
        else:
            {"modem": _write_modem, "jif3d": write_jif3d}[target_format](survey, path)
        payload = {
            "format": target_format, "source_format": source_format,
            "output_sha256": sha256_file(path),
            "stations": [{key: getattr(s, key) for key in STATION_FIELDS} for s in survey.stations],
            "metadata": dict(survey.metadata),
            "complex_observation_count": sum(len(s.active_components) for station in survey.stations for s in station.samples),
        }
        (staging / "conversion.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # Read the actual file and its metadata before exposing a completed output.
        restored = read_observations(path, target_format)
        _check_round_trip(survey, restored)
        staging.rename(target)
    return payload


def _check_round_trip(original: Survey, restored: Survey) -> None:
    def close(a, b):
        # 17-digit text, unit scaling and reciprocal period/frequency incur roundoff.
        return abs(a - b) <= 2e-14 * max(abs(a), abs(b), 1e-300)

    for left, right in zip(original.stations, restored.stations, strict=True):
        if not all(close(a, b) for a, b in ((left.model_x_km, right.model_x_km), (left.model_y_km, right.model_y_km))):
            raise ValueError("Conversion coordinate verification failed")
        first, second = sorted(left.samples, key=lambda s: s.frequency_hz), sorted(right.samples, key=lambda s: s.frequency_hz)
        if len(first) != len(second):
            raise ValueError("Conversion sample count verification failed")
        for a, b in zip(first, second):
            if not close(a.frequency_hz, b.frequency_hz) or a.active_components != b.active_components:
                raise ValueError("Conversion frequency/component verification failed")
            for c in a.active_components:
                if not close(a.values[c], b.values[c]) or not close(a.standard_errors[c], b.standard_errors[c]):
                    raise ValueError(f"Conversion value/error verification failed: {c}")
