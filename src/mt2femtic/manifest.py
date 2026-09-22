"""Run manifest and stage-gate helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

from .model import StageResult


STAGE_NAMES = (
    "source",
    "coordinates",
    "topography",
    "selection",
    "femtic_input",
    "dhexa_input",
    "dhexa_execution",
    "products",
)

DATA_STAGE_NAMES = (
    "source",
    "coordinates",
    "topography",
    "selection",
    "femtic_input",
)

MESH_STAGE_NAMES = (
    "femtic_import",
    "topography",
    "dhexa_input",
    "dhexa_execution",
    "products",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


@dataclass
class RunManifest:
    dataset_id: str
    schema_version: int = 1
    resolved_config: Mapping[str, object] = field(default_factory=dict)
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    executable: Mapping[str, object] = field(default_factory=dict)
    output_hashes: Mapping[str, str] = field(default_factory=dict)
    stages: dict[str, StageResult] = field(default_factory=dict)
    command: str | None = None
    config_kind: str | None = None
    mesh_id: str | None = None
    stage_names: tuple[str, ...] = STAGE_NAMES

    def __post_init__(self) -> None:
        for name in self.stage_names:
            self.stages.setdefault(name, StageResult(name, "not_started", {}, {}, ""))

    def record(self, result: StageResult) -> None:
        if result.name not in self.stage_names:
            raise ValueError(f"Unknown stage: {result.name}")
        self.stages[result.name] = result

    def can_run(self, stage: str, depends_on: tuple[str, ...]) -> bool:
        if stage not in self.stage_names:
            raise ValueError(f"Unknown stage: {stage}")
        return all(
            dependency in self.stages and self.stages[dependency].status == "passed"
            for dependency in depends_on
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "resolved_config": dict(self.resolved_config),
            "input_hashes": dict(self.input_hashes),
            "executable": dict(self.executable),
            "output_hashes": dict(self.output_hashes),
            "stages": {
                name: asdict(self.stages[name])
                for name in self.stage_names
            },
        }
        if self.command is not None:
            payload["command"] = self.command
        if self.config_kind is not None:
            payload["config_kind"] = self.config_kind
        if self.mesh_id is not None:
            payload["mesh_id"] = self.mesh_id
        return payload

    def write(self, path: Path) -> None:
        write_json_atomic(path, self.to_dict())
