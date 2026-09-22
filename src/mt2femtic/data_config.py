"""Strict configuration types for the MT2FEMTIC data command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import (
    RunConfig,
    _boolean,
    _check_keys,
    _integer,
    _nonnegative,
    _number,
    _object,
    _optional_path,
    _path,
    _positive,
    _run_config,
    _text,
)


@dataclass(frozen=True)
class SourceConfig:
    type: str
    path: Path
    impedance_unit: str
    time_convention: str
    allow_time_convention_override: bool
    edi_pattern: str
    edi_list_file: Path | None = None
    station_name_source: str = "edi_metadata"


@dataclass(frozen=True)
class CoordinateConfig:
    geographic_crs: str
    projected_crs: str
    origin_easting_m: float
    origin_northing_m: float
    model_axis_azimuth_deg: float
    vertical_datum_elevation_m: float
    round_trip_tolerance_m: float
    modem_axis_convention: str


@dataclass(frozen=True)
class SelectionConfig:
    periods_s: tuple[float, ...]
    relative_tolerance: float
    impedance_error_floor_fraction: float
    vtf_error_floor_absolute: float
    max_vtf_period_s: float
    frequency_policy: str = "exact"


@dataclass(frozen=True)
class TopographyConfig:
    enabled: bool
    path: Path | None
    columns: str | None


@dataclass(frozen=True)
class ObservationRefinementConfig:
    radius_km: float
    level: int
    weight: float


@dataclass(frozen=True)
class FemticConfig:
    observation_refinement: ObservationRefinementConfig


@dataclass(frozen=True)
class DataConfig:
    schema_version: int
    config_kind: str
    dataset_id: str
    source: SourceConfig
    coordinates: CoordinateConfig
    selection: SelectionConfig
    topography: TopographyConfig
    femtic: FemticConfig
    run: RunConfig


def data_config_from_dict(
    payload: Mapping[str, object], base_dir: Path
) -> DataConfig:
    root = _object(payload, "configuration")
    _check_keys(
        root,
        {
            "schema_version",
            "config_kind",
            "dataset_id",
            "source",
            "coordinates",
            "selection",
            "topography",
            "femtic",
            "run",
        },
        "configuration",
    )
    schema_version = _integer(root["schema_version"], "schema_version")
    if schema_version != 1:
        raise ValueError("schema_version must be 1")
    if root["config_kind"] != "data":
        raise ValueError("config_kind must be data")

    source_data = dict(_object(root["source"], "source"))
    source_data.setdefault("edi_list_file", None)
    source_data.setdefault("station_name_source", "edi_metadata")
    _check_keys(
        source_data,
        {
            "type",
            "path",
            "impedance_unit",
            "time_convention",
            "allow_time_convention_override",
            "edi_pattern",
            "edi_list_file",
            "station_name_source",
        },
        "source",
    )
    source_type = _text(source_data["type"], "source.type").lower()
    if source_type not in {"edi", "modem"}:
        raise ValueError("source.type must be edi or modem")
    impedance_unit = _text(
        source_data["impedance_unit"], "source.impedance_unit"
    ).lower()
    if impedance_unit not in {"ohm", "mv_per_km_per_nt"}:
        raise ValueError("source.impedance_unit must be ohm or mv_per_km_per_nt")
    time_convention = _text(
        source_data["time_convention"], "source.time_convention"
    ).lower()
    if time_convention not in {"exp_minus_iwt", "exp_plus_iwt"}:
        raise ValueError(
            "source.time_convention must be exp_minus_iwt or exp_plus_iwt"
        )
    station_name_source = _text(
        source_data["station_name_source"], "source.station_name_source"
    ).lower()
    if station_name_source not in {"edi_metadata", "file_stem"}:
        raise ValueError(
            "source.station_name_source must be edi_metadata or file_stem"
        )
    source = SourceConfig(
        type=source_type,
        path=_path(source_data["path"], "source.path", base_dir),
        impedance_unit=impedance_unit,
        time_convention=time_convention,
        allow_time_convention_override=_boolean(
            source_data["allow_time_convention_override"],
            "source.allow_time_convention_override",
        ),
        edi_pattern=_text(source_data["edi_pattern"], "source.edi_pattern"),
        edi_list_file=_optional_path(
            source_data["edi_list_file"], "source.edi_list_file", base_dir
        ),
        station_name_source=station_name_source,
    )

    coordinate_data = _object(root["coordinates"], "coordinates")
    _check_keys(
        coordinate_data,
        {
            "geographic_crs",
            "projected_crs",
            "origin_easting_m",
            "origin_northing_m",
            "model_axis_azimuth_deg",
            "vertical_datum_elevation_m",
            "round_trip_tolerance_m",
            "modem_axis_convention",
        },
        "coordinates",
    )
    modem_axes = _text(
        coordinate_data["modem_axis_convention"],
        "coordinates.modem_axis_convention",
    ).lower()
    if modem_axes != "north_east":
        raise ValueError("coordinates.modem_axis_convention must be north_east")
    coordinates = CoordinateConfig(
        geographic_crs=_text(
            coordinate_data["geographic_crs"], "coordinates.geographic_crs"
        ),
        projected_crs=_text(
            coordinate_data["projected_crs"], "coordinates.projected_crs"
        ),
        origin_easting_m=_number(
            coordinate_data["origin_easting_m"], "coordinates.origin_easting_m"
        ),
        origin_northing_m=_number(
            coordinate_data["origin_northing_m"], "coordinates.origin_northing_m"
        ),
        model_axis_azimuth_deg=_number(
            coordinate_data["model_axis_azimuth_deg"],
            "coordinates.model_axis_azimuth_deg",
        ),
        vertical_datum_elevation_m=_number(
            coordinate_data["vertical_datum_elevation_m"],
            "coordinates.vertical_datum_elevation_m",
        ),
        round_trip_tolerance_m=_positive(
            coordinate_data["round_trip_tolerance_m"],
            "coordinates.round_trip_tolerance_m",
        ),
        modem_axis_convention=modem_axes,
    )

    selection_data = _object(root["selection"], "selection")
    _check_keys(
        selection_data,
        {
            "frequency_policy",
            "periods_s",
            "relative_tolerance",
            "impedance_error_floor_fraction",
            "vtf_error_floor_absolute",
            "max_vtf_period_s",
        },
        "selection",
    )
    frequency_policy = _text(
        selection_data["frequency_policy"], "selection.frequency_policy"
    ).lower()
    if frequency_policy not in {"exact", "log_linear"}:
        raise ValueError("selection.frequency_policy must be exact or log_linear")
    raw_periods = selection_data["periods_s"]
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ValueError("selection.periods_s must be a nonempty array")
    periods = tuple(_positive(value, "selection.periods_s") for value in raw_periods)
    if len(set(periods)) != len(periods):
        raise ValueError("selection.periods_s contains duplicates")
    selection = SelectionConfig(
        periods_s=periods,
        relative_tolerance=_positive(
            selection_data["relative_tolerance"], "selection.relative_tolerance"
        ),
        impedance_error_floor_fraction=_nonnegative(
            selection_data["impedance_error_floor_fraction"],
            "selection.impedance_error_floor_fraction",
        ),
        vtf_error_floor_absolute=_nonnegative(
            selection_data["vtf_error_floor_absolute"],
            "selection.vtf_error_floor_absolute",
        ),
        max_vtf_period_s=_positive(
            selection_data["max_vtf_period_s"], "selection.max_vtf_period_s"
        ),
        frequency_policy=frequency_policy,
    )

    topography_data = _object(root["topography"], "topography")
    _check_keys(topography_data, {"enabled", "path", "columns"}, "topography")
    enabled = _boolean(topography_data["enabled"], "topography.enabled")
    topo_path = _optional_path(topography_data["path"], "topography.path", base_dir)
    columns = (
        None
        if topography_data["columns"] is None
        else _text(topography_data["columns"], "topography.columns").lower()
    )
    if enabled and topo_path is None:
        raise ValueError("topography.path is required when topography.enabled is true")
    if enabled and columns not in {
        "longitude_latitude_elevation_m",
        "north_east_elevation_m",
    }:
        raise ValueError("topography.columns is invalid")
    if not enabled and (topo_path is not None or columns is not None):
        raise ValueError("disabled topography requires null path and columns")
    topography = TopographyConfig(enabled, topo_path, columns)

    femtic_data = _object(root["femtic"], "femtic")
    _check_keys(femtic_data, {"observation_refinement"}, "femtic")
    refinement_data = _object(
        femtic_data["observation_refinement"], "femtic.observation_refinement"
    )
    _check_keys(
        refinement_data,
        {"radius_km", "level", "weight"},
        "femtic.observation_refinement",
    )
    level = _integer(
        refinement_data["level"], "femtic.observation_refinement.level"
    )
    if level < 0:
        raise ValueError("femtic.observation_refinement.level must be nonnegative")
    femtic = FemticConfig(
        ObservationRefinementConfig(
            radius_km=_positive(
                refinement_data["radius_km"],
                "femtic.observation_refinement.radius_km",
            ),
            level=level,
            weight=_positive(
                refinement_data["weight"],
                "femtic.observation_refinement.weight",
            ),
        )
    )

    return DataConfig(
        schema_version=schema_version,
        config_kind="data",
        dataset_id=_text(root["dataset_id"], "dataset_id"),
        source=source,
        coordinates=coordinates,
        selection=selection,
        topography=topography,
        femtic=femtic,
        run=_run_config(root["run"]),
    )
