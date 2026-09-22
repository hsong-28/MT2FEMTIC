"""Shared strict JSON configuration loading for MT2FEMTIC."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class RunConfig:
    overwrite: bool
    resume: bool


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _check_keys(data: Mapping[str, object], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        prefix = "" if label == "configuration" else f"{label}."
        raise ValueError(f"Unknown configuration key: {prefix}{unknown[0]}")
    missing = sorted(allowed - set(data))
    if missing:
        prefix = "" if label == "configuration" else f"{label}."
        raise ValueError(f"Missing configuration key: {prefix}{missing[0]}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _path(value: object, label: str, base_dir: Path) -> Path:
    path = Path(_text(value, label))
    return (path if path.is_absolute() else base_dir / path).resolve()


def _optional_path(value: object, label: str, base_dir: Path) -> Path | None:
    return None if value is None else _path(value, label, base_dir)


def _positive(value: object, label: str) -> float:
    result = _number(value, label)
    if result <= 0.0:
        raise ValueError(f"{label} must be positive")
    return result


def _nonnegative(value: object, label: str) -> float:
    result = _number(value, label)
    if result < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return result


def _run_config(value: object) -> RunConfig:
    data = _object(value, "run")
    _check_keys(data, {"overwrite", "resume"}, "run")
    result = RunConfig(
        overwrite=_boolean(data["overwrite"], "run.overwrite"),
        resume=_boolean(data["resume"], "run.resume"),
    )
    if result.overwrite and result.resume:
        raise ValueError("run.overwrite and run.resume cannot both be true")
    return result


def config_from_dict(
    payload: Mapping[str, object], base_dir: Path | None = None
) -> DataConfig | MeshCommandConfig:
    """Parse one strict data or mesh configuration object."""

    root = _object(payload, "configuration")
    kind = root.get("config_kind")
    base = Path.cwd() if base_dir is None else Path(base_dir)
    if kind == "data":
        from .data_config import data_config_from_dict

        return data_config_from_dict(root, base)
    if kind == "mesh":
        from .mesh_config import mesh_config_from_dict

        return mesh_config_from_dict(root, base)
    raise ValueError("config_kind must be 'data' or 'mesh'")


def load_config(path: Path) -> DataConfig | MeshCommandConfig:
    """Load a configuration and resolve its paths beside the JSON file."""

    config_path = Path(path).resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON configuration: {exc}") from exc
    return config_from_dict(_object(payload, "configuration"), config_path.parent)


def config_to_dict(config: object) -> dict[str, object]:
    """Convert a frozen configuration dataclass to JSON-safe values."""

    def convert(value: object) -> object:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {str(key): convert(item) for key, item in value.items()}
        return value

    return convert(asdict(config))  # type: ignore[arg-type,return-value]


from .data_config import (  # noqa: E402  (public configuration types)
    CoordinateConfig,
    DataConfig,
    FemticConfig,
    ObservationRefinementConfig,
    SelectionConfig,
    SourceConfig,
    TopographyConfig,
)
from .mesh_config import (  # noqa: E402  (public configuration types)
    AirLayerConfig,
    ConfiguredAxesConfig,
    GeneratorConfig,
    MeshCommandConfig,
    MeshConfig,
    MeshTopographyConfig,
)
