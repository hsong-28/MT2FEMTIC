"""Select periods and write FEMTIC inputs for one EDI survey."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tempfile

from ..femtic_writer import validate_femtic_inputs, write_femtic_inputs
from ..manifest import sha256_file, write_json_atomic
from ..selection import prepare_selected_survey
from .case import paths_for
from .edi import load_verified_edi_survey
from .projection import project_survey


def _verify_projection_stage(paths, config, station_count: int) -> Path:
    manifest_path = paths.projection / "stage-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Required previous-stage manifest is missing: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "dataset_id": config.dataset_id,
        "resolved_coordinates": asdict(config.coordinates),
        "station_count": station_count,
        "status": "passed",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Projection-stage manifest mismatch: {key}")
    expected_inputs = {
        "1-DataOri/stage-manifest.json": sha256_file(
            paths.original / "stage-manifest.json"
        ),
        "survey.json": sha256_file(paths.root / "survey.json"),
    }
    if manifest.get("input_hashes") != expected_inputs:
        raise ValueError("Projection-stage input hash mismatch")
    output_hashes = manifest.get("output_hashes")
    required_outputs = {"site_xyz.dat", "stations_projected.csv"}
    if not isinstance(output_hashes, dict) or set(output_hashes) != required_outputs:
        raise ValueError("Projection-stage output hash set mismatch")
    for name, digest in output_hashes.items():
        path = paths.projection / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Projection-stage output hash mismatch: {name}")
    return manifest_path


def select_data_stage(root: Path) -> dict[str, object]:
    paths = paths_for(root)
    if paths.selected.exists():
        raise FileExistsError(f"Output already exists: {paths.selected}")
    config, survey = load_verified_edi_survey(root)
    projection_manifest = _verify_projection_stage(
        paths, config, len(survey.stations)
    )
    projected, _ = project_survey(survey, config.coordinates)
    selected = prepare_selected_survey(projected, config.selection)
    staging = Path(tempfile.mkdtemp(prefix=".3-DataSelected4Inv-", dir=paths.root))
    try:
        femtic_paths = write_femtic_inputs(
            selected, staging, config.femtic.observation_refinement
        )
        femtic_validation = validate_femtic_inputs(femtic_paths)
        output_hashes = {
            path.name: sha256_file(path) for path in sorted(staging.iterdir())
        }
        report: dict[str, object] = {
            "dataset_id": config.dataset_id,
            "femtic_validation": femtic_validation,
            "input_hashes": {
                "2-Projection/stage-manifest.json": sha256_file(
                    projection_manifest
                ),
                "survey.json": sha256_file(paths.root / "survey.json"),
            },
            "output_hashes": output_hashes,
            "resolved_femtic": asdict(config.femtic),
            "sample_count": sum(len(station.samples) for station in selected.stations),
            "selection": selected.metadata["selection"],
            "station_count": len(selected.stations),
            "status": "passed",
        }
        write_json_atomic(staging / "stage-manifest.json", report)
        staging.replace(paths.selected)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
