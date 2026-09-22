"""Coordinate, unit, and complex time-convention normalization."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from pyproj import Transformer

from .config import CoordinateConfig
from .manifest import write_json_atomic
from .model import Station


MU0 = 4.0e-7 * math.pi
FIELD_TO_OHM = 1000.0 * MU0
TARGET_TIME_CONVENTION = "exp_minus_iwt"
TARGET_IMPEDANCE_UNIT = "ohm"


def normalize_complex(value: complex, source_convention: str) -> complex:
    if source_convention == TARGET_TIME_CONVENTION:
        return value
    if source_convention == "exp_plus_iwt":
        return value.conjugate()
    raise ValueError(f"Unsupported time convention: {source_convention}")


def impedance_scale_to_ohm(unit: str) -> float:
    if unit == TARGET_IMPEDANCE_UNIT:
        return 1.0
    if unit == "mv_per_km_per_nt":
        return FIELD_TO_OHM
    raise ValueError(f"Unsupported impedance unit: {unit}")


def rotate_to_model_km(
    north_m: float,
    east_m: float,
    azimuth_deg: float,
) -> tuple[float, float]:
    values = (north_m, east_m, azimuth_deg)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Coordinate and azimuth values must be finite")
    angle = math.radians(azimuth_deg)
    x_m = north_m * math.cos(angle) + east_m * math.sin(angle)
    y_m = -north_m * math.sin(angle) + east_m * math.cos(angle)
    return x_m / 1000.0, y_m / 1000.0


def elevation_to_depth_km(
    elevation_m: float,
    datum_elevation_m: float,
) -> float:
    if not math.isfinite(elevation_m) or not math.isfinite(datum_elevation_m):
        raise ValueError("Elevation and vertical datum must be finite")
    return (datum_elevation_m - elevation_m) / 1000.0


def project_station(
    station: Station,
    config: CoordinateConfig,
) -> tuple[Station, float]:
    has_local = station.north_m is not None or station.east_m is not None
    if has_local:
        if station.north_m is None or station.east_m is None:
            raise ValueError(
                f"Station {station.name} must provide both north_m and east_m"
            )
        north_offset_m = float(station.north_m)
        east_offset_m = float(station.east_m)
        round_trip_error_m = 0.0
    else:
        if station.longitude_deg is None or station.latitude_deg is None:
            raise ValueError(
                f"Station {station.name} has neither complete geographic nor local coordinates"
            )
        longitude = float(station.longitude_deg)
        latitude = float(station.latitude_deg)
        if not -180.0 <= longitude <= 180.0:
            raise ValueError(f"Station {station.name} longitude is outside [-180, 180]")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError(f"Station {station.name} latitude is outside [-90, 90]")
        forward = Transformer.from_crs(
            config.geographic_crs,
            config.projected_crs,
            always_xy=True,
        )
        inverse = Transformer.from_crs(
            config.projected_crs,
            config.geographic_crs,
            always_xy=True,
        )
        easting_m, northing_m = forward.transform(longitude, latitude)
        inverse_longitude, inverse_latitude = inverse.transform(easting_m, northing_m)
        checked_easting_m, checked_northing_m = forward.transform(
            inverse_longitude,
            inverse_latitude,
        )
        round_trip_error_m = math.hypot(
            checked_easting_m - easting_m,
            checked_northing_m - northing_m,
        )
        if not math.isfinite(round_trip_error_m):
            raise ValueError(f"Station {station.name} projection produced non-finite values")
        if round_trip_error_m > config.round_trip_tolerance_m:
            raise ValueError(
                f"Station {station.name} projection round-trip error "
                f"{round_trip_error_m:.6g} m exceeds "
                f"{config.round_trip_tolerance_m:.6g} m"
            )
        north_offset_m = northing_m - config.origin_northing_m
        east_offset_m = easting_m - config.origin_easting_m

    model_x_km, model_y_km = rotate_to_model_km(
        north_offset_m,
        east_offset_m,
        config.model_axis_azimuth_deg,
    )
    surface_depth_km = (
        None
        if station.elevation_m is None
        else elevation_to_depth_km(
            station.elevation_m,
            config.vertical_datum_elevation_m,
        )
    )
    return (
        replace(
            station,
            north_m=north_offset_m,
            east_m=east_offset_m,
            model_x_km=model_x_km,
            model_y_km=model_y_km,
            surface_depth_km=surface_depth_km,
        ),
        round_trip_error_m,
    )


def write_convention_audit(path: Path, audit: Mapping[str, object]) -> None:
    write_json_atomic(Path(path), audit)


def convention_audit_payload(
    *,
    source_type: str,
    source_unit: str,
    source_time_convention: str,
    config: CoordinateConfig,
    station_count: int,
    maximum_round_trip_error_m: float,
) -> dict[str, object]:
    return {
        "source_type": source_type,
        "source_impedance_unit": source_unit,
        "target_impedance_unit": TARGET_IMPEDANCE_UNIT,
        "impedance_scale_to_ohm": impedance_scale_to_ohm(source_unit),
        "source_time_convention": source_time_convention,
        "target_time_convention": TARGET_TIME_CONVENTION,
        "complex_conjugation_applied": source_time_convention == "exp_plus_iwt",
        "geographic_crs": config.geographic_crs,
        "projected_crs": config.projected_crs,
        "origin_easting_m": config.origin_easting_m,
        "origin_northing_m": config.origin_northing_m,
        "model_axis_azimuth_deg": config.model_axis_azimuth_deg,
        "vertical_datum_elevation_m": config.vertical_datum_elevation_m,
        "model_coordinate_unit": "km",
        "model_axis_contract": "X=model north axis; Y=model east axis; Z=positive down",
        "horizontal_input_contract": (
            "declared ModEM north/east offsets are authoritative"
            if source_type == "modem"
            else "EDI longitude/latitude projected with declared CRS and origin"
        ),
        "station_count": station_count,
        "maximum_projection_round_trip_error_m": maximum_round_trip_error_m,
    }
