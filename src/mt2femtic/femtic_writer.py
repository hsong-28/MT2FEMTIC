"""Write and validate FEMTIC observation-side input files."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

from .config import ObservationRefinementConfig
from .femtic_io import validate_femtic_files
from .model import ResponseSample, Survey


IMPEDANCE_COMPONENTS = ("ZXX", "ZXY", "ZYX", "ZYY")
VTF_COMPONENTS = ("TX", "TY")
MT_OWNER_ELEMENT = 0
VTF_OWNER_ELEMENT = 1


def _format(value: float) -> str:
    return f"{value:.12g}"


def _component_columns(
    sample: ResponseSample,
    components: tuple[str, ...],
) -> list[float]:
    values: list[float] = []
    errors: list[float] = []
    for component in components:
        if component in sample.active_components:
            value = sample.values[component]
            error = sample.standard_errors[component]
            if not all(
                math.isfinite(number)
                for number in (value.real, value.imag, error)
            ):
                raise ValueError(f"Non-finite FEMTIC value for {component}")
            if error < 0.0:
                raise ValueError(f"Negative active FEMTIC error for {component}")
            values.extend((value.real, value.imag))
            errors.extend((error, error))
        else:
            values.extend((0.0, 0.0))
            errors.extend((-1.0, -1.0))
    return values + errors


def write_femtic_observe(survey: Survey, path: Path, *, precision: int = 12) -> None:
    """Write observation data only, using the same contract as the data stage."""
    def _format(value: float) -> str:
        return f"{value:.{precision}g}"

    station_ids = [station.station_id for station in survey.stations]
    if len(station_ids) != len(set(station_ids)):
        raise ValueError("FEMTIC station IDs must be unique")
    if any(station_id <= 0 for station_id in station_ids):
        raise ValueError("FEMTIC station IDs must be positive")

    lines: list[str] = [f"MT {len(survey.stations)}"]
    for station in survey.stations:
        if station.model_x_km is None or station.model_y_km is None:
            raise ValueError(f"Station {station.name} has no normalized model coordinates")
        mt_samples = [
            sample
            for sample in station.samples
            if sample.active_components.intersection(IMPEDANCE_COMPONENTS)
        ]
        lines.append(
            f"{station.station_id} {station.station_id + 1000} "
            f"{MT_OWNER_ELEMENT} "
            f"{_format(station.model_x_km)} {_format(station.model_y_km)}"
        )
        lines.append(str(len(mt_samples)))
        for sample in mt_samples:
            row = [sample.frequency_hz] + _component_columns(
                sample,
                IMPEDANCE_COMPONENTS,
            )
            lines.append(" ".join(_format(value) for value in row))

    lines.append(f"VTF {len(survey.stations)}")
    for station in survey.stations:
        assert station.model_x_km is not None and station.model_y_km is not None
        vtf_samples = [
            sample
            for sample in station.samples
            if sample.active_components.intersection(VTF_COMPONENTS)
        ]
        magnetic_station_id = station.station_id + 1000
        lines.append(
            f"{magnetic_station_id} {magnetic_station_id} "
            f"{VTF_OWNER_ELEMENT} "
            f"{_format(station.model_x_km)} {_format(station.model_y_km)}"
        )
        lines.append(str(len(vtf_samples)))
        for sample in vtf_samples:
            row = [sample.frequency_hz] + _component_columns(sample, VTF_COMPONENTS)
            lines.append(" ".join(_format(value) for value in row))
    lines.append("END")
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def write_femtic_inputs(
    survey: Survey,
    output_dir: Path,
    refinement: ObservationRefinementConfig,
) -> dict[str, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "observe": destination / "observe.dat",
        "obs_site": destination / "obs_site.dat",
        "distortion": destination / "distortion_iter0.dat",
    }
    write_femtic_observe(survey, paths["observe"])

    site_lines = [str(len(survey.stations))]
    for station in survey.stations:
        assert station.model_x_km is not None and station.model_y_km is not None
        site_lines.extend(
            (
                f"{_format(station.model_x_km)} {_format(station.model_y_km)} 0",
                "1",
                f"{_format(refinement.radius_km)} {refinement.level} "
                f"{_format(refinement.weight)}",
            )
        )
    site_lines.append("0")
    paths["obs_site"].write_text("\n".join(site_lines) + "\n", encoding="ascii")

    distortion_lines = [str(len(survey.stations))]
    distortion_lines.extend(
        f"{station.station_id} 0 0 0 0 0"
        for station in survey.stations
    )
    paths["distortion"].write_text(
        "\n".join(distortion_lines) + "\n",
        encoding="ascii",
    )
    return paths


def validate_femtic_inputs(paths: Mapping[str, Path]) -> dict[str, object]:
    """Validate files just written by MT2FEMTIC using the public reader."""

    return validate_femtic_files(paths)
