"""Read-only preflight validation for data and mesh configurations."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

from .config import DataConfig, MeshCommandConfig, config_to_dict, load_config
from .conventions import convention_audit_payload
from .data_pipeline import _project_survey, _source_survey
from .dhexa import (
    build_configured_axes,
    build_modem_axes,
    validate_station_mesh_bounds,
    verify_generator,
    write_meshgen_input,
)
from .femtic_io import load_femtic_input_set
from .femtic_writer import validate_femtic_inputs, write_femtic_inputs
from .manifest import DATA_STAGE_NAMES, MESH_STAGE_NAMES, RunManifest, sha256_file
from .mesh_pipeline import _resolve_topography, _stations_for_bounds
from .model import StageResult
from .modem_adapter import read_modem_model
from .selection import prepare_selected_survey
from .topography import prepare_topography, validate_topography_coverage


MANIFEST_NAME = "mt2femtic_validation_manifest.json"
LOG_NAME = "mt2femtic_validation.log"


def _log(root: Path, message: str) -> None:
    with (root / LOG_NAME).open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def _hash_outputs(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }


def _start_output(config: DataConfig | MeshCommandConfig, target: Path) -> Path:
    destination = target.resolve()
    if destination.exists() and any(destination.iterdir()):
        if not config.run.overwrite:
            raise FileExistsError(f"Validation output directory is not empty: {destination}")
        marker = destination / MANIFEST_NAME
        if not marker.is_file():
            raise ValueError(
                f"Refusing to overwrite a directory without {MANIFEST_NAME}"
            )
        shutil.rmtree(destination)
    elif destination.exists():
        destination.rmdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.mt2femtic-validate-{uuid.uuid4().hex}"
    staging.mkdir()
    return staging


def _finish(manifest: RunManifest, staging: Path, target: Path, message: str) -> None:
    _log(staging, message)
    manifest.output_hashes = _hash_outputs(staging)
    manifest.write(staging / MANIFEST_NAME)
    staging.replace(target.resolve())


def _pass(
    manifest: RunManifest,
    name: str,
    inputs: dict[str, object],
    outputs: dict[str, object],
    message: str,
) -> None:
    manifest.record(StageResult(name, "passed", inputs, outputs, message))


def _fail(
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
    _finish(
        manifest,
        staging,
        target,
        f"command=validate config_kind={manifest.config_kind} failed_gate={stage} "
        f"status=failed error={type(exc).__name__}: {exc}",
    )
    return manifest


def _validate_data(
    config: DataConfig,
    staging: Path,
    target: Path,
) -> RunManifest:
    manifest = RunManifest(
        dataset_id=config.dataset_id,
        schema_version=config.schema_version,
        resolved_config=config_to_dict(config),
        command="validate",
        config_kind="data",
        stage_names=DATA_STAGE_NAMES,
    )
    try:
        survey = _source_survey(config)
        manifest.input_hashes = dict(survey.metadata.get("input_hashes", {}))
        _pass(
            manifest,
            "source",
            {"path": str(config.source.path)},
            {"station_count": len(survey.stations)},
            "Source input is readable and structurally valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "source", exc)
    try:
        survey, maximum_error = _project_survey(survey, config)
        convention_audit_payload(
            source_type=config.source.type,
            source_unit=config.source.impedance_unit,
            source_time_convention=config.source.time_convention,
            config=config.coordinates,
            station_count=len(survey.stations),
            maximum_round_trip_error_m=maximum_error,
        )
        _pass(
            manifest,
            "coordinates",
            {},
            {"maximum_round_trip_error_m": maximum_error},
            "Coordinate conversion and round trip are valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "coordinates", exc)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            topography = prepare_topography(
                config.topography,
                config.coordinates,
                Path(temporary) / "topography.dat",
            )
        if config.topography.path is not None:
            manifest.input_hashes = {
                **dict(manifest.input_hashes),
                str(config.topography.path): sha256_file(config.topography.path),
            }
        _pass(
            manifest,
            "topography",
            {"enabled": config.topography.enabled},
            {"point_count": topography.point_count},
            "Data topography input is valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "topography", exc)
    try:
        survey = prepare_selected_survey(survey, config.selection)
        _pass(
            manifest,
            "selection",
            {"frequency_policy": config.selection.frequency_policy},
            {
                "sample_count": sum(
                    len(station.samples) for station in survey.stations
                )
            },
            "Frequency selection and error-floor policy are valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "selection", exc)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_femtic_inputs(
                survey,
                Path(temporary),
                config.femtic.observation_refinement,
            )
            summary = validate_femtic_inputs(paths)
        _pass(
            manifest,
            "femtic_input",
            {},
            summary,
            "Temporary FEMTIC serialization is structurally valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "femtic_input", exc)
    _finish(
        manifest,
        staging,
        target,
        f"command=validate config_kind=data dataset={config.dataset_id} status=passed",
    )
    return manifest


def _validate_mesh(
    config: MeshCommandConfig,
    data_root: Path,
    staging: Path,
    target: Path,
) -> RunManifest:
    manifest = RunManifest(
        dataset_id=config.mesh_id,
        schema_version=config.schema_version,
        resolved_config=config_to_dict(config),
        command="validate",
        config_kind="mesh",
        mesh_id=config.mesh_id,
        stage_names=MESH_STAGE_NAMES,
    )
    try:
        inputs = load_femtic_input_set(
            data_root, config.mesh.station_coordinate_tolerance_km
        )
        manifest.input_hashes = dict(inputs.source_hashes)
        _pass(
            manifest,
            "femtic_import",
            {"data_root": str(data_root), "layout": inputs.layout},
            dict(inputs.audit),
            "FEMTIC data directory is valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "femtic_import", exc)
    try:
        topography_source, topography = _resolve_topography(config, inputs)
        if topography_source is not None:
            manifest.input_hashes = {
                **dict(manifest.input_hashes),
                str(topography_source): sha256_file(topography_source),
            }
        _pass(
            manifest,
            "topography",
            {"mode": config.topography.mode},
            {"point_count": topography.point_count},
            "Mesh topography choice is valid",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "topography", exc)
    try:
        model = None
        if config.mesh.geometry_mode == "configured_axes":
            axes = build_configured_axes(config.mesh)
        else:
            assert config.mesh.modem_model_path is not None
            model = read_modem_model(config.mesh.modem_model_path)
            axes = build_modem_axes(model, config.mesh)
            manifest.input_hashes = {
                **dict(manifest.input_hashes),
                str(config.mesh.modem_model_path): sha256_file(
                    config.mesh.modem_model_path
                ),
            }
        stations = _stations_for_bounds(inputs)
        validate_station_mesh_bounds(stations, axes)
        validate_topography_coverage(
            topography,
            stations,
            (axes.x_km[0], axes.x_km[-1], axes.y_km[0], axes.y_km[-1]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            staged_topography = None
            if topography_source is not None:
                staged_topography = work / "topography.dat"
                shutil.copy2(topography_source, staged_topography)
            write_meshgen_input(
                axes,
                config.mesh,
                config.topography,
                work / "meshgen.inp",
                staged_topography,
            )
        identity = verify_generator(config.generator)
        manifest.executable = {
            "path": str(identity.path),
            "version": identity.version,
            "sha256": identity.sha256,
        }
        _pass(
            manifest,
            "dhexa_input",
            {},
            {
                "axis_divisions": [
                    len(axes.x_km) - 1,
                    len(axes.y_km) - 1,
                    len(axes.z_km) - 1,
                ],
                "model_mapping_required": model is not None,
            },
            "DHEXA input and generator identity are valid; generator was not run",
        )
    except Exception as exc:
        return _fail(manifest, staging, target, "dhexa_input", exc)
    _finish(
        manifest,
        staging,
        target,
        f"command=validate config_kind=mesh mesh={config.mesh_id} status=passed",
    )
    return manifest


def validate(
    config_path: Path,
    output_root: Path,
    data_root: Path | None = None,
) -> RunManifest:
    """Validate one command configuration without publishing scientific files."""

    config = load_config(config_path)
    if isinstance(config, DataConfig):
        if data_root is not None:
            raise ValueError(
                "data configuration does not accept --data; expected: "
                "mt2femtic validate --config CONFIG --output OUTPUT"
            )
    else:
        if data_root is None:
            raise ValueError(
                "mesh configuration requires --data; expected: mt2femtic validate "
                "--config CONFIG --data DATA_DIRECTORY --output OUTPUT"
            )
        data_root = Path(data_root).resolve()
    target = Path(output_root).resolve()
    staging = _start_output(config, target)
    if isinstance(config, DataConfig):
        return _validate_data(config, staging, target)
    assert data_root is not None
    return _validate_mesh(config, data_root, staging, target)
