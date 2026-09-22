"""Independent EDI/ModEM to FEMTIC data-package pipeline."""

from __future__ import annotations

import csv
import json
import re
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .config import DataConfig, config_to_dict, load_config
from .conventions import (
    convention_audit_payload,
    project_station,
    write_convention_audit,
)
from .edi_adapter import read_edi_directory
from .femtic_writer import validate_femtic_inputs, write_femtic_inputs
from .manifest import DATA_STAGE_NAMES, RunManifest, sha256_file, write_json_atomic
from .model import StageResult, Station, Survey
from .modem_adapter import read_modem_data
from .qa_plot import plot_period_coverage, plot_station_topography
from .selection import prepare_selected_survey
from .topography import prepare_topography


MANIFEST_NAME = "mt2femtic_data_manifest.json"
LOG_NAME = "mt2femtic_data.log"
_NATURAL_NAME_PART = re.compile(r"(\d+)")


def _append_log(root: Path, message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with (root / LOG_NAME).open("a", encoding="utf-8") as stream:
        stream.write(f"{timestamp} {message}\n")


def _hash_outputs(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }


def _manifest_from_payload(payload: dict[str, object]) -> RunManifest:
    manifest = RunManifest(
        dataset_id=str(payload["dataset_id"]),
        schema_version=int(payload.get("schema_version", 1)),
        resolved_config=payload.get("resolved_config", {}),
        input_hashes=payload.get("input_hashes", {}),
        executable=payload.get("executable", {}),
        output_hashes=payload.get("output_hashes", {}),
        command="data",
        config_kind="data",
        stage_names=DATA_STAGE_NAMES,
    )
    stages = payload.get("stages", {})
    if isinstance(stages, dict):
        for name, value in stages.items():
            if name in DATA_STAGE_NAMES and isinstance(value, dict):
                manifest.record(
                    StageResult(
                        name,
                        value.get("status", "not_started"),
                        value.get("inputs", {}),
                        value.get("outputs", {}),
                        value.get("message", ""),
                    )
                )
    return manifest


def _verify_resume(config: DataConfig, target: Path) -> RunManifest | None:
    if not config.run.resume:
        return None
    marker = target / MANIFEST_NAME
    if not target.exists():
        return None
    if not target.is_dir() or not marker.is_file():
        raise ValueError(
            f"Resume requires a readable {MANIFEST_NAME} with matching hashes"
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Resume manifest is invalid: {marker}") from exc
    if payload.get("command") != "data" or payload.get("config_kind") != "data":
        raise ValueError("Resume target is not an MT2FEMTIC data output")
    if payload.get("resolved_config") != config_to_dict(config):
        raise ValueError("Resume configuration does not match the existing manifest")
    input_hashes = payload.get("input_hashes")
    output_hashes = payload.get("output_hashes")
    if not isinstance(input_hashes, dict) or not isinstance(output_hashes, dict):
        raise ValueError("Resume manifest hash mappings are invalid")
    for source, expected in input_hashes.items():
        path = Path(source)
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Resume input hash mismatch: {source}")
    for relative, expected in output_hashes.items():
        path = target / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Resume output hash mismatch: {relative}")
    return _manifest_from_payload(payload)


def _start_output(config: DataConfig, target: Path) -> Path:
    destination = target.resolve()
    if destination.exists() and any(destination.iterdir()):
        if config.run.resume:
            raise ValueError(
                f"Resume requires a readable {MANIFEST_NAME} with matching hashes"
            )
        if not config.run.overwrite:
            raise FileExistsError(f"Output directory is not empty: {destination}")
        marker = destination / MANIFEST_NAME
        if not marker.is_file():
            raise ValueError(
                f"Refusing to overwrite a directory without {MANIFEST_NAME}"
            )
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Existing data manifest is invalid: {marker}") from exc
        if (
            payload.get("command") != "data"
            or payload.get("dataset_id") != config.dataset_id
        ):
            raise ValueError("Refusing to overwrite output from a different dataset")
        shutil.rmtree(destination)
    elif destination.exists():
        destination.rmdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.mt2femtic-data-{uuid.uuid4().hex}"
    staging.mkdir()
    return staging


def _finish_output(staging: Path, target: Path) -> None:
    destination = target.resolve()
    if destination.exists():
        raise FileExistsError(f"Cannot finalize over existing output: {destination}")
    staging.replace(destination)


def _record_pass(
    manifest: RunManifest,
    name: str,
    inputs: dict[str, object],
    outputs: dict[str, object],
    message: str,
) -> None:
    manifest.record(StageResult(name, "passed", inputs, outputs, message))


def _finalize_failure(
    manifest: RunManifest,
    staging: Path,
    target: Path,
    stage: str,
    exc: Exception,
) -> RunManifest:
    manifest.record(
        StageResult(stage, "failed", {}, {}, f"{type(exc).__name__}: {exc}")
    )
    shutil.rmtree(staging)
    staging.mkdir()
    _append_log(
        staging,
        f"command=data dataset={manifest.dataset_id} stage={stage} status=failed "
        f"error={type(exc).__name__}: {exc}",
    )
    manifest.output_hashes = _hash_outputs(staging)
    manifest.write(staging / MANIFEST_NAME)
    _finish_output(staging, target)
    return manifest


def _natural_station_key(name: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in _NATURAL_NAME_PART.split(name)
        if part
    )


def _normalize_station_order(survey: Survey) -> Survey:
    ordered = sorted(
        survey.stations,
        key=lambda station: (_natural_station_key(station.name), station.name),
    )
    metadata = dict(survey.metadata)
    metadata["station_order"] = "case-insensitive natural station-name order"
    return replace(
        survey,
        stations=tuple(
            replace(station, station_id=station_id)
            for station_id, station in enumerate(ordered, start=1)
        ),
        metadata=metadata,
    )


def _source_survey(config: DataConfig) -> Survey:
    if config.source.type == "edi":
        survey = read_edi_directory(config.source)
    else:
        survey = read_modem_data(config.source.path, config.source)
    return _normalize_station_order(survey)


def _project_survey(survey: Survey, config: DataConfig) -> tuple[Survey, float]:
    stations: list[Station] = []
    maximum_error = 0.0
    locations: set[tuple[float, float]] = set()
    for station in survey.stations:
        projected, error_m = project_station(station, config.coordinates)
        assert projected.model_x_km is not None and projected.model_y_km is not None
        location = (projected.model_x_km, projected.model_y_km)
        if location in locations:
            raise ValueError(
                f"Duplicate projected station location at {location[0]:g}, "
                f"{location[1]:g} km"
            )
        locations.add(location)
        stations.append(projected)
        maximum_error = max(maximum_error, error_m)
    return replace(survey, stations=tuple(stations)), maximum_error


def _write_station_projection(path: Path, stations: tuple[Station, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "station_id",
                "station_name",
                "longitude_deg",
                "latitude_deg",
                "elevation_m",
                "north_offset_m",
                "east_offset_m",
                "model_x_km",
                "model_y_km",
                "surface_depth_km",
            )
        )
        for station in stations:
            writer.writerow(
                (
                    station.station_id,
                    station.name,
                    station.longitude_deg,
                    station.latitude_deg,
                    station.elevation_m,
                    station.north_m,
                    station.east_m,
                    station.model_x_km,
                    station.model_y_km,
                    station.surface_depth_km,
                )
            )


def data(config_path: Path, output_root: Path) -> RunManifest:
    """Create one immutable, independently reviewable FEMTIC data package."""

    loaded = load_config(config_path)
    if not isinstance(loaded, DataConfig):
        raise ValueError("data command requires config_kind=data")
    config = loaded
    target = Path(output_root).resolve()
    resumed = _verify_resume(config, target)
    if resumed is not None:
        return resumed
    staging = _start_output(config, target)
    manifest = RunManifest(
        config.dataset_id,
        schema_version=config.schema_version,
        resolved_config=config_to_dict(config),
        command="data",
        config_kind="data",
        stage_names=DATA_STAGE_NAMES,
    )
    _append_log(staging, f"command=data dataset={config.dataset_id} status=started")

    try:
        survey = _source_survey(config)
        manifest.input_hashes = dict(survey.metadata.get("input_hashes", {}))
        write_json_atomic(
            staging / "original" / "source_inventory.json",
            {
                "source_type": config.source.type,
                "station_count": len(survey.stations),
                "input_hashes": dict(manifest.input_hashes),
                "normalization_metadata": {
                    key: value
                    for key, value in survey.metadata.items()
                    if key != "input_hashes"
                },
            },
        )
        _record_pass(
            manifest,
            "source",
            {"type": config.source.type},
            {"station_count": len(survey.stations)},
            "Source parsed and normalized",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "source", exc)

    try:
        survey, maximum_error = _project_survey(survey, config)
        _write_station_projection(
            staging / "projection" / "stations_projected.csv", survey.stations
        )
        audit = convention_audit_payload(
            source_type=config.source.type,
            source_unit=config.source.impedance_unit,
            source_time_convention=config.source.time_convention,
            config=config.coordinates,
            station_count=len(survey.stations),
            maximum_round_trip_error_m=maximum_error,
        )
        write_convention_audit(staging / "coordinate_convention_audit.json", audit)
        _record_pass(
            manifest,
            "coordinates",
            {},
            {"maximum_round_trip_error_m": maximum_error},
            "Coordinates follow the FEMTIC model-axis contract",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "coordinates", exc)

    try:
        topography = prepare_topography(
            config.topography,
            config.coordinates,
            staging / "projection" / "topography.dat",
        )
        _record_pass(
            manifest,
            "topography",
            {"enabled": config.topography.enabled},
            {"point_count": topography.point_count},
            "Topography input is valid",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "topography", exc)

    try:
        survey = prepare_selected_survey(survey, config.selection)
        sample_count = sum(len(station.samples) for station in survey.stations)
        _record_pass(
            manifest,
            "selection",
            {"frequency_policy": config.selection.frequency_policy},
            {
                "sample_count": sample_count,
                "selection": survey.metadata.get("selection", {}),
            },
            "Frequencies selected and error floors applied",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "selection", exc)

    try:
        femtic_paths = write_femtic_inputs(
            survey,
            staging / "inversion_input",
            config.femtic.observation_refinement,
        )
        femtic_summary = validate_femtic_inputs(femtic_paths)
        plot_station_topography(
            survey.stations,
            topography,
            staging / "qa" / "station_topography.png",
            config.dataset_id,
            config.coordinates.projected_crs,
            config.coordinates.model_axis_azimuth_deg,
        )
        plot_period_coverage(
            survey.stations,
            config.selection.periods_s,
            staging / "qa" / "period_coverage.png",
            config.dataset_id,
        )
        _record_pass(
            manifest,
            "femtic_input",
            {},
            femtic_summary,
            "FEMTIC inputs and QA plots passed validation",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "femtic_input", exc)

    _append_log(staging, f"command=data dataset={config.dataset_id} status=passed")
    manifest.output_hashes = _hash_outputs(staging)
    manifest.write(staging / MANIFEST_NAME)
    _finish_output(staging, target)
    return manifest
