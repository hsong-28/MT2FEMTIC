"""Project one normalized EDI survey into FEMTIC model coordinates."""

from __future__ import annotations

import csv
from dataclasses import asdict, replace
from pathlib import Path
import shutil
import tempfile

from ..conventions import project_station
from ..data_config import CoordinateConfig
from ..manifest import sha256_file, write_json_atomic
from ..model import Station, Survey
from .case import paths_for
from .edi import load_verified_edi_survey


def project_survey(survey: Survey, config: CoordinateConfig) -> tuple[Survey, float]:
    stations: list[Station] = []
    locations: set[tuple[float, float]] = set()
    maximum_error = 0.0
    for station in survey.stations:
        projected, error_m = project_station(station, config)
        location = (float(projected.model_x_km), float(projected.model_y_km))
        if location in locations:
            raise ValueError(
                f"Duplicate projected station location at {location[0]:g}, "
                f"{location[1]:g} km"
            )
        locations.add(location)
        stations.append(projected)
        maximum_error = max(maximum_error, error_m)
    return replace(survey, stations=tuple(stations)), maximum_error


def _write_coordinates(output: Path, stations: tuple[Station, ...]) -> None:
    fields = (
        "station_id", "station_name", "longitude_deg", "latitude_deg",
        "elevation_m", "north_offset_m", "east_offset_m", "model_x_km",
        "model_y_km", "surface_depth_km",
    )
    with (output / "stations_projected.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream, (output / "site_xyz.dat").open("w", encoding="ascii") as xyz:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for station in stations:
            values = (
                station.station_id, station.name, station.longitude_deg,
                station.latitude_deg, station.elevation_m, station.north_m,
                station.east_m, station.model_x_km, station.model_y_km,
                station.surface_depth_km,
            )
            writer.writerow(values)
            xyz.write(
                f"{station.model_y_km:.12g}\t{station.model_x_km:.12g}\t"
                f"{station.elevation_m:.12g}\t{station.station_id}\t{station.name}\n"
            )


def project_sites_stage(root: Path) -> dict[str, object]:
    paths = paths_for(root)
    if paths.projection.exists():
        raise FileExistsError(f"Output already exists: {paths.projection}")
    config, survey = load_verified_edi_survey(root)
    previous_manifest = paths.original / "stage-manifest.json"
    projected, maximum_error = project_survey(survey, config.coordinates)
    staging = Path(tempfile.mkdtemp(prefix=".2-Projection-", dir=paths.root))
    try:
        _write_coordinates(staging, projected.stations)
        output_hashes = {
            path.name: sha256_file(path) for path in sorted(staging.iterdir())
        }
        input_hashes = {
            "1-DataOri/stage-manifest.json": sha256_file(previous_manifest),
            "survey.json": sha256_file(paths.root / "survey.json"),
        }
        report: dict[str, object] = {
            "coordinate_contract": {
                "horizontal_unit": "km",
                "model_x": "north_at_zero_azimuth",
                "model_y": "east_at_zero_azimuth",
                "site_xyz_columns": "model_y_km,model_x_km,elevation_m,station_id,station_name",
                "vertical": "depth_positive_down",
            },
            "dataset_id": config.dataset_id,
            "input_hashes": input_hashes,
            "maximum_round_trip_error_m": maximum_error,
            "output_hashes": output_hashes,
            "resolved_coordinates": asdict(config.coordinates),
            "station_count": len(projected.stations),
            "status": "passed",
        }
        write_json_atomic(staging / "stage-manifest.json", report)
        staging.replace(paths.projection)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
