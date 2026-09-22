"""Topography conversion and coverage validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pyproj import Transformer

from .config import CoordinateConfig, TopographyConfig
from .conventions import elevation_to_depth_km, rotate_to_model_km
from .model import Station


@dataclass(frozen=True)
class TopographyPoint:
    model_x_km: float
    model_y_km: float
    depth_km: float
    elevation_m: float


@dataclass(frozen=True)
class TopographySummary:
    enabled: bool
    output_path: Path | None
    points: tuple[TopographyPoint, ...]
    bounds_km: tuple[float, float, float, float] | None

    @property
    def point_count(self) -> int:
        return len(self.points)


def _read_rows(path: Path) -> list[tuple[float, float, float]]:
    rows: list[tuple[float, float, float]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if len(tokens) != 3:
            raise ValueError(
                f"Topography line {line_number} must contain exactly three columns"
            )
        try:
            row = tuple(float(token) for token in tokens)
        except ValueError as exc:
            raise ValueError(f"Topography line {line_number} is not numeric") from exc
        if not all(math.isfinite(value) for value in row):
            raise ValueError(f"Topography line {line_number} contains non-finite values")
        rows.append((row[0], row[1], row[2]))
    if not rows:
        raise ValueError(f"Topography file contains no data rows: {path}")
    return rows


def prepare_topography(
    config: TopographyConfig,
    coordinates: CoordinateConfig,
    output_path: Path,
) -> TopographySummary:
    destination = Path(output_path)
    if not config.enabled:
        return TopographySummary(False, None, (), None)
    if config.path is None or config.columns is None:
        raise ValueError("Enabled topography requires path and column convention")
    if not config.path.is_file():
        raise FileNotFoundError(f"Topography file does not exist: {config.path}")
    rows = _read_rows(config.path)
    transformer = None
    if config.columns == "longitude_latitude_elevation_m":
        transformer = Transformer.from_crs(
            coordinates.geographic_crs,
            coordinates.projected_crs,
            always_xy=True,
        )
    elif config.columns != "north_east_elevation_m":
        raise ValueError(f"Unsupported topography column convention: {config.columns}")

    points: list[TopographyPoint] = []
    horizontal_locations: set[tuple[float, float]] = set()
    for first, second, elevation_m in rows:
        if transformer is None:
            north_offset_m = first
            east_offset_m = second
        else:
            longitude_deg = first
            latitude_deg = second
            if not -180.0 <= longitude_deg <= 180.0 or not -90.0 <= latitude_deg <= 90.0:
                raise ValueError("Topography longitude or latitude is outside its valid range")
            easting_m, northing_m = transformer.transform(longitude_deg, latitude_deg)
            north_offset_m = northing_m - coordinates.origin_northing_m
            east_offset_m = easting_m - coordinates.origin_easting_m
        model_x_km, model_y_km = rotate_to_model_km(
            north_offset_m,
            east_offset_m,
            coordinates.model_axis_azimuth_deg,
        )
        depth_km = elevation_to_depth_km(
            elevation_m,
            coordinates.vertical_datum_elevation_m,
        )
        location = (model_x_km, model_y_km)
        if location in horizontal_locations:
            raise ValueError(
                f"Duplicate projected topography location: {model_x_km:g}, {model_y_km:g} km"
            )
        horizontal_locations.add(location)
        points.append(TopographyPoint(model_x_km, model_y_km, depth_km, elevation_m))

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(
            f"{point.model_x_km:.12g} {point.model_y_km:.12g} {point.depth_km:.12g}\n"
            for point in points
        ),
        encoding="ascii",
    )
    x_values = [point.model_x_km for point in points]
    y_values = [point.model_y_km for point in points]
    return TopographySummary(
        enabled=True,
        output_path=destination,
        points=tuple(points),
        bounds_km=(min(x_values), max(x_values), min(y_values), max(y_values)),
    )


def read_normalized_topography(path: Path) -> TopographySummary:
    """Read model-X, model-Y, positive-down depth rows already in kilometres."""

    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Normalized topography file does not exist: {source}")
    rows = _read_rows(source)
    points: list[TopographyPoint] = []
    horizontal_locations: set[tuple[float, float]] = set()
    for model_x_km, model_y_km, depth_km in rows:
        location = (model_x_km, model_y_km)
        if location in horizontal_locations:
            raise ValueError(
                f"Duplicate normalized topography location: {model_x_km:g}, "
                f"{model_y_km:g} km"
            )
        horizontal_locations.add(location)
        points.append(
            TopographyPoint(
                model_x_km=model_x_km,
                model_y_km=model_y_km,
                depth_km=depth_km,
                elevation_m=-1000.0 * depth_km,
            )
        )
    x_values = [point.model_x_km for point in points]
    y_values = [point.model_y_km for point in points]
    return TopographySummary(
        enabled=True,
        output_path=source,
        points=tuple(points),
        bounds_km=(min(x_values), max(x_values), min(y_values), max(y_values)),
    )


def validate_topography_coverage(
    summary: TopographySummary,
    stations: Sequence[Station],
    mesh_bounds_km: tuple[float, float, float, float],
) -> None:
    if not summary.enabled:
        return
    if summary.bounds_km is None:
        raise ValueError("Enabled topography has no bounds")
    minimum_x, maximum_x, minimum_y, maximum_y = summary.bounds_km
    for station in stations:
        if station.model_x_km is None or station.model_y_km is None:
            raise ValueError(f"Station {station.name} has no normalized coordinates")
        if not (
            minimum_x <= station.model_x_km <= maximum_x
            and minimum_y <= station.model_y_km <= maximum_y
        ):
            raise ValueError(f"Topography does not cover station {station.name}")
    mesh_minimum_x, mesh_maximum_x, mesh_minimum_y, mesh_maximum_y = mesh_bounds_km
    if not (
        minimum_x <= mesh_minimum_x
        and maximum_x >= mesh_maximum_x
        and minimum_y <= mesh_minimum_y
        and maximum_y >= mesh_maximum_y
    ):
        raise ValueError("Topography does not cover the horizontal mesh bounds")
