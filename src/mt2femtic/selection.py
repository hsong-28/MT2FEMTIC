"""Frequency selection and explicit error floors."""

from __future__ import annotations

import math
from dataclasses import replace

from .config import SelectionConfig
from .model import ResponseSample, Station, Survey
from .resampling import resample_survey_log_frequency


IMPEDANCE_COMPONENTS = frozenset({"ZXX", "ZXY", "ZYX", "ZYY"})
VTF_COMPONENTS = frozenset({"TX", "TY"})


def period_matches(actual_period_s: float, target_period_s: float, rtol: float) -> bool:
    return abs(actual_period_s - target_period_s) <= rtol * max(
        abs(actual_period_s),
        abs(target_period_s),
    )


def apply_impedance_floor(
    sample: ResponseSample,
    fraction: float,
) -> ResponseSample:
    if not math.isfinite(fraction) or fraction < 0.0:
        raise ValueError("Impedance error-floor fraction must be finite and nonnegative")
    errors = dict(sample.standard_errors)
    floor = 0.0
    if {"ZXY", "ZYX"}.issubset(sample.active_components):
        floor = fraction * math.sqrt(
            abs(sample.values["ZXY"]) * abs(sample.values["ZYX"])
        )
    for component in IMPEDANCE_COMPONENTS & sample.active_components:
        source_error = errors.get(component)
        if source_error is None or not math.isfinite(source_error) or source_error < 0.0:
            raise ValueError(f"Invalid standard error for {component}")
        errors[component] = max(source_error, floor)
    return replace(sample, standard_errors=errors)


def _apply_vtf_floor(
    sample: ResponseSample,
    absolute_floor: float,
) -> ResponseSample:
    if not math.isfinite(absolute_floor) or absolute_floor < 0.0:
        raise ValueError("VTF error floor must be finite and nonnegative")
    errors = dict(sample.standard_errors)
    for component in VTF_COMPONENTS & sample.active_components:
        source_error = errors.get(component)
        if source_error is None or not math.isfinite(source_error) or source_error < 0.0:
            raise ValueError(f"Invalid standard error for {component}")
        errors[component] = max(source_error, absolute_floor)
    return replace(sample, standard_errors=errors)


def _without_long_period_vtf(
    sample: ResponseSample,
    max_vtf_period_s: float,
) -> ResponseSample:
    period_s = 1.0 / sample.frequency_hz
    if period_s <= max_vtf_period_s:
        return sample
    active = sample.active_components - VTF_COMPONENTS
    return replace(
        sample,
        values={key: value for key, value in sample.values.items() if key in active},
        standard_errors={
            key: value for key, value in sample.standard_errors.items() if key in active
        },
        active_components=frozenset(active),
    )


def select_survey(survey: Survey, config: SelectionConfig) -> Survey:
    requested_matches = {period_s: 0 for period_s in config.periods_s}
    selected_stations: list[Station] = []
    for station in survey.stations:
        selected_samples: list[ResponseSample] = []
        for target_period_s in config.periods_s:
            matches = [
                sample
                for sample in station.samples
                if period_matches(
                    1.0 / sample.frequency_hz,
                    target_period_s,
                    config.relative_tolerance,
                )
            ]
            if len(matches) > 1:
                raise ValueError(
                    f"Station {station.name} has multiple matches for requested "
                    f"period {target_period_s:g} s"
                )
            if not matches:
                continue
            requested_matches[target_period_s] += 1
            sample = _without_long_period_vtf(
                matches[0],
                config.max_vtf_period_s,
            )
            sample = apply_impedance_floor(
                sample,
                config.impedance_error_floor_fraction,
            )
            sample = _apply_vtf_floor(sample, config.vtf_error_floor_absolute)
            selected_samples.append(sample)
        selected_stations.append(replace(station, samples=tuple(selected_samples)))
    for period_s, count in requested_matches.items():
        if count == 0:
            raise ValueError(f"Requested period {period_s:g} s is unavailable")
    metadata = dict(survey.metadata)
    metadata["selection"] = {
        "frequency_policy": config.frequency_policy,
        "periods_s": list(config.periods_s),
        "relative_tolerance": config.relative_tolerance,
        "impedance_error_floor_fraction": config.impedance_error_floor_fraction,
        "vtf_error_floor_absolute": config.vtf_error_floor_absolute,
        "max_vtf_period_s": config.max_vtf_period_s,
        "matching_station_counts": {
            f"{period_s:.12g}": count
            for period_s, count in requested_matches.items()
        },
    }
    return replace(
        survey,
        stations=tuple(selected_stations),
        metadata=metadata,
    )


def prepare_selected_survey(survey: Survey, config: SelectionConfig) -> Survey:
    """Apply the declared frequency policy, component masks, and error floors."""

    if config.frequency_policy == "exact":
        return select_survey(survey, config)
    if config.frequency_policy == "log_linear":
        target_frequencies = tuple(1.0 / period_s for period_s in config.periods_s)
        resampled = resample_survey_log_frequency(survey, target_frequencies)
        return select_survey(resampled, config)
    raise ValueError("selection.frequency_policy must be exact or log_linear")
