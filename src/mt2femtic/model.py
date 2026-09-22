"""Normalized scientific data types used by every PrepareData3D stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping


@dataclass(frozen=True)
class ResponseSample:
    frequency_hz: float
    values: Mapping[str, complex]
    standard_errors: Mapping[str, float]
    active_components: frozenset[str]


@dataclass(frozen=True)
class Station:
    station_id: int
    name: str
    longitude_deg: float | None
    latitude_deg: float | None
    elevation_m: float | None
    north_m: float | None
    east_m: float | None
    model_x_km: float | None
    model_y_km: float | None
    surface_depth_km: float | None
    samples: tuple[ResponseSample, ...]


@dataclass(frozen=True)
class Survey:
    stations: tuple[Station, ...]
    impedance_unit: str
    time_convention: str
    source_type: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ModemModel:
    dimensions: tuple[int, int, int]
    north_widths_m: tuple[float, ...]
    east_widths_m: tuple[float, ...]
    depth_widths_m: tuple[float, ...]
    resistivity_ohm_m: tuple[float, ...]
    origin_m: tuple[float, float, float]
    rotation_deg: float
    representation: str


@dataclass(frozen=True)
class StageResult:
    name: str
    status: Literal["not_started", "passed", "failed"]
    inputs: Mapping[str, object]
    outputs: Mapping[str, object]
    message: str
