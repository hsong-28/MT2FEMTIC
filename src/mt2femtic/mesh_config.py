"""Strict configuration types for the MT2FEMTIC DHEXA mesh command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import (
    RunConfig,
    _check_keys,
    _integer,
    _nonnegative,
    _object,
    _optional_path,
    _path,
    _positive,
    _run_config,
    _text,
)


@dataclass(frozen=True)
class ConfiguredAxesConfig:
    x_max_km: float
    x_uniform_limit_km: float
    x_spacing_km: float
    x_padding_factor: float
    y_max_km: float
    y_uniform_limit_km: float
    y_spacing_km: float
    y_padding_factor: float
    z_max_km: float
    z_spacing_km: float
    z_padding_factor: float


@dataclass(frozen=True)
class AirLayerConfig:
    scheme: str
    count: int
    top_km: float
    bottom_km: float | None


@dataclass(frozen=True)
class MeshConfig:
    backend: str
    geometry_mode: str
    configured_axes: ConfiguredAxesConfig | None
    modem_model_path: Path | None
    observation_refinement: str
    air_layers: AirLayerConfig
    initial_resistivity_ohm_m: float
    air_resistivity_ohm_m: float
    sea_resistivity_ohm_m: float
    model_round_trip_tolerance: float
    station_coordinate_tolerance_km: float


@dataclass(frozen=True)
class MeshTopographyConfig:
    mode: str
    path: Path | None
    max_interpolation_points: int
    search_radius_km: float
    tolerance_km: float

    @property
    def enabled(self) -> bool:
        return self.mode != "flat"


@dataclass(frozen=True)
class GeneratorConfig:
    path: Path
    version: str
    sha256: str
    timeout_s: int


@dataclass(frozen=True)
class MeshCommandConfig:
    schema_version: int
    config_kind: str
    mesh_id: str
    mesh: MeshConfig
    topography: MeshTopographyConfig
    generator: GeneratorConfig
    run: RunConfig


def _configured_axes(value: object) -> ConfiguredAxesConfig:
    data = _object(value, "mesh.configured_axes")
    keys = {
        "x_max_km",
        "x_uniform_limit_km",
        "x_spacing_km",
        "x_padding_factor",
        "y_max_km",
        "y_uniform_limit_km",
        "y_spacing_km",
        "y_padding_factor",
        "z_max_km",
        "z_spacing_km",
        "z_padding_factor",
    }
    _check_keys(data, keys, "mesh.configured_axes")
    axes = ConfiguredAxesConfig(
        x_max_km=_positive(data["x_max_km"], "mesh.configured_axes.x_max_km"),
        x_uniform_limit_km=_nonnegative(
            data["x_uniform_limit_km"], "mesh.configured_axes.x_uniform_limit_km"
        ),
        x_spacing_km=_positive(
            data["x_spacing_km"], "mesh.configured_axes.x_spacing_km"
        ),
        x_padding_factor=_positive(
            data["x_padding_factor"], "mesh.configured_axes.x_padding_factor"
        ),
        y_max_km=_positive(data["y_max_km"], "mesh.configured_axes.y_max_km"),
        y_uniform_limit_km=_nonnegative(
            data["y_uniform_limit_km"], "mesh.configured_axes.y_uniform_limit_km"
        ),
        y_spacing_km=_positive(
            data["y_spacing_km"], "mesh.configured_axes.y_spacing_km"
        ),
        y_padding_factor=_positive(
            data["y_padding_factor"], "mesh.configured_axes.y_padding_factor"
        ),
        z_max_km=_positive(data["z_max_km"], "mesh.configured_axes.z_max_km"),
        z_spacing_km=_positive(
            data["z_spacing_km"], "mesh.configured_axes.z_spacing_km"
        ),
        z_padding_factor=_positive(
            data["z_padding_factor"], "mesh.configured_axes.z_padding_factor"
        ),
    )
    if min(axes.x_padding_factor, axes.y_padding_factor, axes.z_padding_factor) <= 1:
        raise ValueError("mesh padding factors must be greater than 1")
    return axes


def mesh_config_from_dict(
    payload: Mapping[str, object], base_dir: Path
) -> MeshCommandConfig:
    root = _object(payload, "configuration")
    _check_keys(
        root,
        {
            "schema_version",
            "config_kind",
            "mesh_id",
            "mesh",
            "topography",
            "generator",
            "run",
        },
        "configuration",
    )
    schema_version = _integer(root["schema_version"], "schema_version")
    if schema_version != 1:
        raise ValueError("schema_version must be 1")
    if root["config_kind"] != "mesh":
        raise ValueError("config_kind must be mesh")

    mesh_data = _object(root["mesh"], "mesh")
    _check_keys(
        mesh_data,
        {
            "backend",
            "geometry_mode",
            "configured_axes",
            "modem_model_path",
            "observation_refinement",
            "air_layers",
            "initial_resistivity_ohm_m",
            "air_resistivity_ohm_m",
            "sea_resistivity_ohm_m",
            "model_round_trip_tolerance",
            "station_coordinate_tolerance_km",
        },
        "mesh",
    )
    backend = _text(mesh_data["backend"], "mesh.backend").lower()
    if backend != "dhexa":
        raise ValueError("mesh.backend must be dhexa")
    geometry_mode = _text(mesh_data["geometry_mode"], "mesh.geometry_mode").lower()
    if geometry_mode not in {"configured_axes", "modem_model_axes"}:
        raise ValueError(
            "mesh.geometry_mode must be configured_axes or modem_model_axes"
        )
    axes = (
        None
        if mesh_data["configured_axes"] is None
        else _configured_axes(mesh_data["configured_axes"])
    )
    model_path = _optional_path(
        mesh_data["modem_model_path"], "mesh.modem_model_path", base_dir
    )
    observation_refinement = _text(
        mesh_data["observation_refinement"], "mesh.observation_refinement"
    ).lower()
    if observation_refinement not in {"from_data", "none"}:
        raise ValueError("mesh.observation_refinement must be from_data or none")
    air_data = _object(mesh_data["air_layers"], "mesh.air_layers")
    _check_keys(
        air_data,
        {"scheme", "count", "top_km", "bottom_km"},
        "mesh.air_layers",
    )
    air_scheme = _text(air_data["scheme"], "mesh.air_layers.scheme").lower()
    if air_scheme not in {"geometric", "modem_fixed_height"}:
        raise ValueError(
            "mesh.air_layers.scheme must be geometric or modem_fixed_height"
        )
    air_count = _integer(air_data["count"], "mesh.air_layers.count")
    if air_count < 1:
        raise ValueError("mesh.air_layers.count must be at least 1")
    air_bottom = (
        _positive(air_data["bottom_km"], "mesh.air_layers.bottom_km")
        if air_scheme == "geometric"
        else None
    )
    if air_scheme == "modem_fixed_height" and air_data["bottom_km"] is not None:
        raise ValueError(
            "mesh.air_layers.bottom_km must be null for modem_fixed_height"
        )
    air_layers = AirLayerConfig(
        scheme=air_scheme,
        count=air_count,
        top_km=_positive(air_data["top_km"], "mesh.air_layers.top_km"),
        bottom_km=air_bottom,
    )
    if air_layers.bottom_km is not None and air_layers.top_km <= air_layers.bottom_km:
        raise ValueError("mesh.air_layers.top_km must exceed bottom_km")
    if air_scheme == "modem_fixed_height" and geometry_mode != "modem_model_axes":
        raise ValueError(
            "modem_fixed_height requires mesh.geometry_mode=modem_model_axes"
        )
    if geometry_mode == "configured_axes":
        if axes is None:
            raise ValueError("mesh.configured_axes is required for configured_axes")
        if model_path is not None:
            raise ValueError("mesh.modem_model_path must be null for configured_axes")
    else:
        if model_path is None:
            raise ValueError("mesh.modem_model_path is required for modem_model_axes")
        if axes is not None:
            raise ValueError("mesh.configured_axes must be null for modem_model_axes")
    mesh = MeshConfig(
        backend=backend,
        geometry_mode=geometry_mode,
        configured_axes=axes,
        modem_model_path=model_path,
        observation_refinement=observation_refinement,
        air_layers=air_layers,
        initial_resistivity_ohm_m=_positive(
            mesh_data["initial_resistivity_ohm_m"],
            "mesh.initial_resistivity_ohm_m",
        ),
        air_resistivity_ohm_m=_positive(
            mesh_data["air_resistivity_ohm_m"], "mesh.air_resistivity_ohm_m"
        ),
        sea_resistivity_ohm_m=_positive(
            mesh_data["sea_resistivity_ohm_m"], "mesh.sea_resistivity_ohm_m"
        ),
        model_round_trip_tolerance=_positive(
            mesh_data["model_round_trip_tolerance"],
            "mesh.model_round_trip_tolerance",
        ),
        station_coordinate_tolerance_km=_positive(
            mesh_data["station_coordinate_tolerance_km"],
            "mesh.station_coordinate_tolerance_km",
        ),
    )

    topography_data = _object(root["topography"], "topography")
    _check_keys(
        topography_data,
        {
            "mode",
            "path",
            "max_interpolation_points",
            "search_radius_km",
            "tolerance_km",
        },
        "topography",
    )
    mode = _text(topography_data["mode"], "topography.mode").lower()
    if mode not in {"flat", "native", "file"}:
        raise ValueError("topography.mode must be flat, native, or file")
    topo_path = _optional_path(
        topography_data["path"], "topography.path", base_dir
    )
    if mode == "file" and topo_path is None:
        raise ValueError("topography.path is required for topography.mode=file")
    if mode != "file" and topo_path is not None:
        raise ValueError("topography.path is allowed only for topography.mode=file")
    max_points = _integer(
        topography_data["max_interpolation_points"],
        "topography.max_interpolation_points",
    )
    if max_points <= 0:
        raise ValueError("topography.max_interpolation_points must be positive")
    topography = MeshTopographyConfig(
        mode=mode,
        path=topo_path,
        max_interpolation_points=max_points,
        search_radius_km=_positive(
            topography_data["search_radius_km"], "topography.search_radius_km"
        ),
        tolerance_km=_positive(
            topography_data["tolerance_km"], "topography.tolerance_km"
        ),
    )

    generator_data = _object(root["generator"], "generator")
    _check_keys(
        generator_data, {"path", "version", "sha256", "timeout_s"}, "generator"
    )
    digest = _text(generator_data["sha256"], "generator.sha256").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("generator.sha256 must contain 64 hexadecimal characters")
    timeout = _integer(generator_data["timeout_s"], "generator.timeout_s")
    if timeout <= 0:
        raise ValueError("generator.timeout_s must be positive")
    generator = GeneratorConfig(
        path=_path(generator_data["path"], "generator.path", base_dir),
        version=_text(generator_data["version"], "generator.version"),
        sha256=digest,
        timeout_s=timeout,
    )

    return MeshCommandConfig(
        schema_version=schema_version,
        config_kind="mesh",
        mesh_id=_text(root["mesh_id"], "mesh_id"),
        mesh=mesh,
        topography=topography,
        generator=generator,
        run=_run_config(root["run"]),
    )
