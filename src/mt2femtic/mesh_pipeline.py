"""Independent FEMTIC input to DHEXA hexahedral-mesh pipeline."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import MeshCommandConfig, config_to_dict, load_config
from .dhexa import (
    MeshAxes,
    build_configured_axes,
    build_modem_axes,
    run_generator,
    validate_dhexa_products,
    validate_station_mesh_bounds,
    verify_generator,
    verify_model_round_trip,
    write_meshgen_input,
    write_source_model_block,
)
from .femtic_io import FemticInputSet, load_femtic_input_set
from .manifest import MESH_STAGE_NAMES, RunManifest, sha256_file, write_json_atomic
from .model import ModemModel, StageResult, Station
from .modem_adapter import read_modem_model
from .topography import (
    TopographySummary,
    read_normalized_topography,
    validate_topography_coverage,
)


MANIFEST_NAME = "mt2femtic_mesh_manifest.json"
LOG_NAME = "mt2femtic_mesh.log"


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
        dataset_id=str(payload.get("dataset_id", payload["mesh_id"])),
        schema_version=int(payload.get("schema_version", 1)),
        resolved_config=payload.get("resolved_config", {}),
        input_hashes=payload.get("input_hashes", {}),
        executable=payload.get("executable", {}),
        output_hashes=payload.get("output_hashes", {}),
        command="mesh",
        config_kind="mesh",
        mesh_id=str(payload["mesh_id"]),
        stage_names=MESH_STAGE_NAMES,
    )
    stages = payload.get("stages", {})
    if isinstance(stages, dict):
        for name, value in stages.items():
            if name in MESH_STAGE_NAMES and isinstance(value, dict):
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


def _verify_resume(
    config: MeshCommandConfig,
    data_root: Path,
    target: Path,
) -> RunManifest | None:
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
    if payload.get("command") != "mesh" or payload.get("config_kind") != "mesh":
        raise ValueError("Resume target is not an MT2FEMTIC mesh output")
    if payload.get("resolved_config") != config_to_dict(config):
        raise ValueError("Resume configuration does not match the existing manifest")
    import_stage = payload.get("stages", {}).get("femtic_import", {})  # type: ignore[union-attr]
    recorded_root = import_stage.get("inputs", {}).get("data_root")  # type: ignore[union-attr]
    if recorded_root != str(data_root.resolve()):
        raise ValueError("Resume data root does not match the existing manifest")
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


def _start_output(config: MeshCommandConfig, target: Path) -> Path:
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
            raise ValueError(f"Existing mesh manifest is invalid: {marker}") from exc
        if payload.get("command") != "mesh" or payload.get("mesh_id") != config.mesh_id:
            raise ValueError("Refusing to overwrite output from a different mesh")
        shutil.rmtree(destination)
    elif destination.exists():
        destination.rmdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.mt2femtic-mesh-{uuid.uuid4().hex}"
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
        f"command=mesh mesh={manifest.mesh_id} stage={stage} status=failed "
        f"error={type(exc).__name__}: {exc}",
    )
    manifest.output_hashes = _hash_outputs(staging)
    manifest.write(staging / MANIFEST_NAME)
    _finish_output(staging, target)
    return manifest


def _stations_for_bounds(inputs: FemticInputSet) -> tuple[Station, ...]:
    return tuple(
        Station(
            station_id=station.station_id,
            name=f"FEMTIC-{station.station_id}",
            longitude_deg=None,
            latitude_deg=None,
            elevation_m=None,
            north_m=None,
            east_m=None,
            model_x_km=station.model_x_km,
            model_y_km=station.model_y_km,
            surface_depth_km=None,
            samples=(),
        )
        for station in inputs.stations
    )


def _build_axes(config: MeshCommandConfig) -> tuple[MeshAxes, ModemModel | None]:
    if config.mesh.geometry_mode == "configured_axes":
        return build_configured_axes(config.mesh), None
    assert config.mesh.modem_model_path is not None
    model = read_modem_model(config.mesh.modem_model_path)
    return build_modem_axes(model, config.mesh), model


def _mesh_bounds(axes: MeshAxes) -> tuple[float, float, float, float]:
    return axes.x_km[0], axes.x_km[-1], axes.y_km[0], axes.y_km[-1]


def _resolve_topography(
    config: MeshCommandConfig,
    inputs: FemticInputSet,
) -> tuple[Path | None, TopographySummary]:
    if config.topography.mode == "flat":
        return None, TopographySummary(False, None, (), None)
    if config.topography.mode == "native":
        if inputs.topography_path is None:
            raise ValueError("Native FEMTIC data package contains no topography.dat")
        source = inputs.topography_path
    else:
        assert config.topography.path is not None
        source = config.topography.path
    return source.resolve(), read_normalized_topography(source)


def mesh(config_path: Path, data_root: Path, output_root: Path) -> RunManifest:
    """Create one DHEXA mesh without modifying the supplied FEMTIC data."""

    loaded = load_config(config_path)
    if not isinstance(loaded, MeshCommandConfig):
        raise ValueError("mesh command requires config_kind=mesh")
    config = loaded
    data_path = Path(data_root).resolve()
    target = Path(output_root).resolve()
    resumed = _verify_resume(config, data_path, target)
    if resumed is not None:
        return resumed
    staging = _start_output(config, target)
    manifest = RunManifest(
        dataset_id=config.mesh_id,
        schema_version=config.schema_version,
        resolved_config=config_to_dict(config),
        command="mesh",
        config_kind="mesh",
        mesh_id=config.mesh_id,
        stage_names=MESH_STAGE_NAMES,
    )
    _append_log(staging, f"command=mesh mesh={config.mesh_id} status=started")

    try:
        inputs = load_femtic_input_set(
            data_path, config.mesh.station_coordinate_tolerance_km
        )
        manifest.input_hashes = dict(inputs.source_hashes)
        _record_pass(
            manifest,
            "femtic_import",
            {"data_root": str(data_path), "layout": inputs.layout},
            dict(inputs.audit),
            "FEMTIC data input passed structural and hash validation",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "femtic_import", exc)

    try:
        topography_source, topography = _resolve_topography(config, inputs)
        selected_topography_hash = None
        if topography_source is not None:
            selected_topography_hash = sha256_file(topography_source)
            manifest.input_hashes = {
                **dict(manifest.input_hashes),
                str(topography_source): selected_topography_hash,
            }
        _record_pass(
            manifest,
            "topography",
            {
                "mode": config.topography.mode,
                "source_path": str(topography_source) if topography_source else None,
            },
            {
                "point_count": topography.point_count,
                "source_sha256": selected_topography_hash,
            },
            "Mesh topography choice is valid",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "topography", exc)

    work_dir = staging / "dhexa"
    try:
        axes, model = _build_axes(config)
        if config.mesh.modem_model_path is not None:
            manifest.input_hashes = {
                **dict(manifest.input_hashes),
                str(config.mesh.modem_model_path): sha256_file(
                    config.mesh.modem_model_path
                ),
            }
        stations = _stations_for_bounds(inputs)
        validate_station_mesh_bounds(stations, axes)
        validate_topography_coverage(topography, stations, _mesh_bounds(axes))
        work_dir.mkdir(parents=True)
        staged_observation = work_dir / "obs_site.dat"
        if config.mesh.observation_refinement == "from_data":
            shutil.copy2(inputs.paths["obs_site"], staged_observation)
        else:
            staged_observation.write_text("0\n0\n", encoding="ascii")
        staged_topography = None
        if topography_source is not None:
            staged_topography = work_dir / "topography.dat"
            shutil.copy2(topography_source, staged_topography)
        write_meshgen_input(
            axes,
            config.mesh,
            config.topography,
            work_dir / "meshgen.inp",
            staged_topography,
        )
        identity = verify_generator(config.generator)
        manifest.executable = {
            "path": str(identity.path),
            "version": identity.version,
            "sha256": identity.sha256,
        }
        write_json_atomic(
            staging / "input_audit.json",
            {
                "layout": inputs.layout,
                "data_root": str(data_path),
                "source_paths": {
                    key: str(path.resolve()) for key, path in inputs.paths.items()
                },
                "source_hashes": dict(manifest.input_hashes),
                "femtic_audit": dict(inputs.audit),
                "observation_refinement": config.mesh.observation_refinement,
                "topography_mode": config.topography.mode,
                "topography_source": (
                    str(topography_source) if topography_source else None
                ),
                "staged_input_hashes": {
                    "dhexa/obs_site.dat": sha256_file(staged_observation),
                    **(
                        {"dhexa/topography.dat": sha256_file(staged_topography)}
                        if staged_topography is not None
                        else {}
                    ),
                },
            },
        )
        _record_pass(
            manifest,
            "dhexa_input",
            {},
            {
                "axis_divisions": [
                    len(axes.x_km) - 1,
                    len(axes.y_km) - 1,
                    len(axes.z_km) - 1,
                ],
                "observation_refinement": config.mesh.observation_refinement,
                "meshgen": "dhexa/meshgen.inp",
            },
            "DHEXA input and executable identity are valid",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "dhexa_input", exc)

    try:
        run_generator(identity, work_dir)
        _record_pass(
            manifest,
            "dhexa_execution",
            {},
            {"stdout": "dhexa/meshgen.stdout", "stderr": "dhexa/meshgen.stderr"},
            "Canonical DHEXA generator completed",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "dhexa_execution", exc)

    try:
        summary = validate_dhexa_products(work_dir)
        outputs: dict[str, object] = {
            "node_count": summary.node_count,
            "element_count": summary.element_count,
            "parameter_count": summary.parameter_count,
            "active_parameter_count": summary.active_parameter_count,
            "minimum_resistivity_ohm_m": summary.minimum_resistivity_ohm_m,
            "maximum_resistivity_ohm_m": summary.maximum_resistivity_ohm_m,
        }
        if model is not None:
            mapped = work_dir / "resistivity_block_source_model.dat"
            outputs["model_mapping"] = write_source_model_block(
                model, work_dir, mapped
            )
            outputs["model_round_trip_max_relative_difference"] = (
                verify_model_round_trip(
                    model,
                    work_dir,
                    mapped,
                    config.mesh.model_round_trip_tolerance,
                )
            )
        _record_pass(
            manifest,
            "products",
            {},
            outputs,
            "DHEXA products passed structural and model-mapping validation",
        )
    except Exception as exc:
        return _finalize_failure(manifest, staging, target, "products", exc)

    _append_log(staging, f"command=mesh mesh={config.mesh_id} status=passed")
    manifest.output_hashes = _hash_outputs(staging)
    manifest.write(staging / MANIFEST_NAME)
    _finish_output(staging, target)
    return manifest
