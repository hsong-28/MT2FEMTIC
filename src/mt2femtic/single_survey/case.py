"""Single-survey paths and the read-only input gate."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from ..config import DataConfig, load_config
from ..edi_adapter import resolve_edi_files


TEMPORAL_FIELDS = {"year", "time_step", "baseline_survey", "repeat_survey"}


@dataclass(frozen=True)
class SingleSurveyPaths:
    root: Path
    edi: Path
    original: Path
    projection: Path
    selected: Path
    mesh: Path

    @property
    def outputs(self) -> tuple[Path, ...]:
        return self.original, self.projection, self.selected, self.mesh


def paths_for(root: Path) -> SingleSurveyPaths:
    case = Path(root).resolve()
    return SingleSurveyPaths(
        root=case,
        edi=case / "0-EDI",
        original=case / "1-DataOri",
        projection=case / "2-Projection",
        selected=case / "3-DataSelected4Inv",
        mesh=case / "4-MeshGeneration",
    )


def _check_temporal_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in TEMPORAL_FIELDS:
                raise ValueError(f"Temporal field is not allowed: {key}")
            _check_temporal_fields(child)
    elif isinstance(value, list):
        for child in value:
            _check_temporal_fields(child)


def load_survey(root: Path) -> DataConfig:
    config_path = paths_for(root).root / "survey.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    _check_temporal_fields(payload)
    config = load_config(config_path)
    if not isinstance(config, DataConfig):
        raise ValueError("survey.json must contain a data configuration")
    return config


def check_input(root: Path) -> dict[str, object]:
    paths = paths_for(root)
    config = load_survey(root)
    if config.source.type != "edi":
        raise ValueError("This is the EDI-only implementation stage")
    if config.source.path.resolve() != paths.edi:
        raise ValueError("source.path must be 0-EDI")
    files = resolve_edi_files(config.source)
    return {
        "dataset_id": config.dataset_id,
        "edi_file_count": len(files),
        "edi_inventory": (
            config.source.edi_list_file.name
            if config.source.edi_list_file is not None
            else "pattern"
        ),
        "station_name_source": config.source.station_name_source,
        "source_type": "edi",
    }
