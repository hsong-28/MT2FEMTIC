"""Write, but do not execute, DHEXA inputs for one selected survey."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tempfile

from ..config import DataConfig, MeshCommandConfig, load_config
from ..dhexa import (
    MeshAxes,
    build_configured_axes,
    build_modem_axes,
    validate_station_mesh_bounds,
    write_meshgen_input,
)
from ..femtic_io import FemticInputSet, load_femtic_input_set
from ..manifest import sha256_file, write_json_atomic
from ..model import Station
from ..modem_adapter import read_modem_model
from .case import paths_for
from .edi import load_verified_edi_survey


def _inside(root: Path, path: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the survey root") from exc


def _verify_selection_stage(
    root: Path, coordinate_tolerance_km: float
) -> tuple[DataConfig, FemticInputSet, Path]:
    paths = paths_for(root)
    data_config, survey = load_verified_edi_survey(root)
    manifest_path = paths.selected / "stage-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Required previous-stage manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "dataset_id": data_config.dataset_id,
        "resolved_femtic": asdict(data_config.femtic),
        "station_count": len(survey.stations),
        "status": "passed",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Selection-stage manifest mismatch: {key}")
    expected_inputs = {
        "2-Projection/stage-manifest.json": sha256_file(paths.projection / "stage-manifest.json"),
        "survey.json": sha256_file(paths.root / "survey.json"),
    }
    if manifest.get("input_hashes") != expected_inputs:
        raise ValueError("Selection-stage input hash mismatch")
    output_hashes = manifest.get("output_hashes")
    required = {"observe.dat", "obs_site.dat", "distortion_iter0.dat"}
    if not isinstance(output_hashes, dict) or set(output_hashes) != required:
        raise ValueError("Selection-stage output hash set mismatch")
    for name, digest in output_hashes.items():
        path = paths.selected / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Selection-stage output hash mismatch: {name}")
    inputs = load_femtic_input_set(paths.selected, coordinate_tolerance_km)
    if inputs.audit != manifest.get("femtic_validation"):
        raise ValueError("Selection-stage FEMTIC validation mismatch")
    return data_config, inputs, manifest_path


def _axes(config: MeshCommandConfig, root: Path) -> tuple[MeshAxes, Path | None]:
    if config.mesh.geometry_mode == "configured_axes":
        return build_configured_axes(config.mesh), None
    assert config.mesh.modem_model_path is not None
    model_path = config.mesh.modem_model_path
    _inside(root, model_path, "mesh.modem_model_path")
    if not model_path.is_file():
        raise FileNotFoundError(f"ModEM model does not exist: {model_path}")
    model = read_modem_model(model_path)
    return build_modem_axes(model, config.mesh), model_path


def _stations(inputs: FemticInputSet) -> tuple[Station, ...]:
    return tuple(
        Station(s.station_id, f"FEMTIC-{s.station_id}", None, None, None, None,
                None, s.model_x_km, s.model_y_km, None, ())
        for s in inputs.stations
    )


def write_meshgen_stage(root: Path) -> dict[str, object]:
    paths = paths_for(root)
    if paths.mesh.exists():
        raise FileExistsError(f"Output already exists: {paths.mesh}")
    config_path = paths.root / "mesh.json"
    loaded = load_config(config_path)
    if not isinstance(loaded, MeshCommandConfig):
        raise ValueError("mesh.json must contain a mesh configuration")
    config = loaded
    if config.run.overwrite or config.run.resume:
        raise ValueError("Staged mesh preparation requires run.overwrite=false and run.resume=false")
    data_config, inputs, selection_manifest = _verify_selection_stage(
        paths.root, config.mesh.station_coordinate_tolerance_km
    )
    generator_path = _inside(paths.root, config.generator.path, "generator.path")
    axes, model_path = _axes(config, paths.root)
    validate_station_mesh_bounds(_stations(inputs), axes)

    topography_source: Path | None = None
    if config.topography.mode == "native":
        if inputs.topography_path is None:
            raise ValueError("Stage 03 contains no native topography.dat")
        topography_source = inputs.topography_path
    elif config.topography.mode == "file":
        assert config.topography.path is not None
        topography_source = config.topography.path
    if topography_source is not None:
        _inside(paths.root, topography_source, "topography.path")
        if not topography_source.is_file():
            raise FileNotFoundError(f"Topography file does not exist: {topography_source}")
        if topography_source.name in {"meshgen.inp", "obs_site.dat", "stage-manifest.json"}:
            raise ValueError("Topography filename conflicts with a stage output")

    staging = Path(tempfile.mkdtemp(prefix=".4-MeshGeneration-", dir=paths.root))
    try:
        staged_obs = staging / "obs_site.dat"
        if config.mesh.observation_refinement == "from_data":
            shutil.copy2(inputs.paths["obs_site"], staged_obs)
        else:
            staged_obs.write_text("0\n0\n", encoding="ascii")
        staged_topography = None
        if topography_source is not None:
            staged_topography = staging / topography_source.name
            shutil.copy2(topography_source, staged_topography)
        write_meshgen_input(
            axes, config.mesh, config.topography, staging / "meshgen.inp", staged_topography
        )
        input_hashes = {
            "3-DataSelected4Inv/stage-manifest.json": sha256_file(selection_manifest),
            "mesh.json": sha256_file(config_path),
        }
        if model_path is not None:
            input_hashes[_inside(paths.root, model_path, "mesh.modem_model_path")] = sha256_file(model_path)
        if topography_source is not None:
            input_hashes[_inside(paths.root, topography_source, "topography.path")] = sha256_file(topography_source)
        output_hashes = {
            path.name: sha256_file(path) for path in sorted(staging.iterdir()) if path.is_file()
        }
        report: dict[str, object] = {
            "axis_bounds_km": {
                "x": [axes.x_km[0], axes.x_km[-1]],
                "y": [axes.y_km[0], axes.y_km[-1]],
                "z": [axes.z_km[0], axes.z_km[-1]],
            },
            "axis_divisions": [len(axes.x_km) - 1, len(axes.y_km) - 1, len(axes.z_km) - 1],
            "dataset_id": data_config.dataset_id,
            "execution_status": "not_run",
            "generator": {
                "path": generator_path,
                "sha256": config.generator.sha256,
                "status": "declared_not_verified",
                "version": config.generator.version,
            },
            "geometry_mode": config.mesh.geometry_mode,
            "input_hashes": input_hashes,
            "mesh_id": config.mesh_id,
            "observation_refinement": config.mesh.observation_refinement,
            "output_hashes": output_hashes,
            "station_count": len(inputs.stations),
            "status": "passed",
            "topography_mode": config.topography.mode,
        }
        write_json_atomic(staging / "stage-manifest.json", report)
        staging.replace(paths.mesh)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
