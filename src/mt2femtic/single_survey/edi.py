"""Write normalized original data for one EDI survey."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile

from ..data_config import DataConfig
from ..edi_adapter import read_edi_directory
from ..manifest import sha256_file, write_json_atomic
from ..model import ResponseSample, Station, Survey
from .case import check_input, load_survey, paths_for


IMPEDANCE = ("ZXX", "ZXY", "ZYX", "ZYY")
VTF = ("TX", "TY")
MU0 = 4.0 * math.pi * 1.0e-7


def _gfmt(value: float) -> str:
    return f"{float(value):15.7g}"


def _response_row(sample: ResponseSample, components: tuple[str, ...]) -> str:
    row = [sample.frequency_hz]
    for component in components:
        value = sample.values.get(component, 0.0j)
        row.extend((value.real, value.imag))
    for component in components:
        error = sample.standard_errors.get(component, 0.0)
        row.extend((error, error))
    return "".join(_gfmt(value) for value in row) + "\n"


def _write_original_files(survey: Survey, output: Path) -> None:
    locations = (output / "site_lonlat.dat").open("w", encoding="ascii")
    impedance = (output / "imp_ori.dat").open("w", encoding="ascii")
    vtf = (output / "tip_ori.dat").open("w", encoding="ascii")
    try:
        impedance.write(f"{'MT':>3s}{len(survey.stations):4d}\n")
        vtf.write(f"{'VTF':>3s}{len(survey.stations):4d}\n")
        for station in survey.stations:
            _write_station(station, locations, impedance, vtf)
        impedance.write("END\n")
        vtf.write("END\n")
    finally:
        locations.close()
        impedance.close()
        vtf.close()
    maximum = max(len(station.samples) for station in survey.stations)
    (output / "frefile.dat").write_text(f"{maximum}\n", encoding="ascii")
    _write_unit_check(survey, output / "check_unit.dat")


def _write_station(station: Station, locations, impedance, vtf) -> None:
    if None in (station.longitude_deg, station.latitude_deg, station.elevation_m):
        raise ValueError(f"Station coordinates are incomplete: {station.name}")
    index = station.station_id
    latitude = float(station.latitude_deg)
    longitude = float(station.longitude_deg)
    impedance.write(f"{index:5d}{index + 1000:5d}{0:2d}{latitude:15.7g}{longitude:15.7g}\n")
    impedance.write(f"{len(station.samples)}\n")
    impedance.writelines(_response_row(sample, IMPEDANCE) for sample in station.samples)
    has_vtf = any(
        abs(sample.values.get(component, 0.0j)) > 0.0
        for sample in station.samples
        for component in VTF
    )
    samples = station.samples if has_vtf else ()
    vtf.write(f"{index + 1000:5d}{index + 1000:5d}{1:2d}{latitude:15.7g}{longitude:15.7g}\n")
    vtf.write(f"{len(samples)}\n")
    vtf.writelines(_response_row(sample, VTF) for sample in samples)
    locations.write(
        f"{longitude:15.7g}{latitude:15.7g}{float(station.elevation_m):15.7g}"
        f"{index:4d} {station.name:30s}\n"
    )


def _write_unit_check(survey: Survey, path: Path) -> None:
    with path.open("w", encoding="ascii") as stream:
        for station in survey.stations:
            means = []
            for component in ("ZXY", "ZYX"):
                rho = [
                    abs(sample.values[component]) ** 2
                    / (2.0 * math.pi * sample.frequency_hz * MU0)
                    for sample in station.samples
                    if component in sample.active_components
                ]
                means.append(sum(rho) / len(rho) if rho else 0.0)
            if any(value >= 1.0e6 or value <= 1.0 for value in means):
                stream.write(
                    f"{station.station_id} {station.name} {means[0]:g} {means[1]:g}\n"
                )


def read_edi_stage(root: Path) -> dict[str, object]:
    paths = paths_for(root)
    if paths.original.exists():
        raise FileExistsError(f"Output already exists: {paths.original}")
    check_input(root)
    config = load_survey(root)
    survey = read_edi_directory(config.source)
    input_hashes = {
        Path(source).resolve().relative_to(paths.root).as_posix(): digest
        for source, digest in survey.metadata["input_hashes"].items()
    }
    staging = Path(tempfile.mkdtemp(prefix=".1-DataOri-", dir=paths.root))
    try:
        _write_original_files(survey, staging)
        output_hashes = {
            path.name: sha256_file(path) for path in sorted(staging.iterdir())
        }
        report: dict[str, object] = {
            "dataset_id": config.dataset_id,
            "input_hashes": input_hashes,
            "output_hashes": output_hashes,
            "output_impedance_unit": survey.impedance_unit,
            "output_time_convention": survey.time_convention,
            "sample_count": sum(len(station.samples) for station in survey.stations),
            "source_impedance_unit": config.source.impedance_unit,
            "source_time_convention": config.source.time_convention,
            "station_count": len(survey.stations),
            "station_name_source": config.source.station_name_source,
            "status": "passed",
        }
        write_json_atomic(staging / "stage-manifest.json", report)
        staging.replace(paths.original)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_verified_edi_survey(root: Path) -> tuple[DataConfig, Survey]:
    """Reload immutable EDI input after verifying the completed read stage."""

    paths = paths_for(root)
    check_input(root)
    config = load_survey(root)
    survey = read_edi_directory(config.source)
    manifest_path = paths.original / "stage-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Required previous-stage manifest is missing: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "dataset_id": config.dataset_id,
        "output_impedance_unit": survey.impedance_unit,
        "output_time_convention": survey.time_convention,
        "source_impedance_unit": config.source.impedance_unit,
        "source_time_convention": config.source.time_convention,
        "station_name_source": config.source.station_name_source,
        "status": "passed",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Previous-stage manifest mismatch: {key}")
    source_hashes = {
        Path(path).resolve().relative_to(paths.root).as_posix(): digest
        for path, digest in survey.metadata["input_hashes"].items()
    }
    if manifest.get("input_hashes") != source_hashes:
        raise ValueError("Previous-stage source hash mismatch")
    output_hashes = manifest.get("output_hashes")
    required_outputs = {
        "check_unit.dat", "frefile.dat", "imp_ori.dat", "site_lonlat.dat",
        "tip_ori.dat",
    }
    if not isinstance(output_hashes, dict) or set(output_hashes) != required_outputs:
        raise ValueError("Previous-stage output hash set mismatch")
    for name, digest in output_hashes.items():
        path = paths.original / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Previous-stage output hash mismatch: {name}")
    return config, survey
