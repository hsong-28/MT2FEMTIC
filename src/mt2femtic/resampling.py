"""Explicit frequency-grid resampling for normalized 3-D surveys."""

from __future__ import annotations

import bisect
import math
from dataclasses import replace

from .model import ResponseSample, Survey


def _frequency_key(value: float) -> str:
    return f"{value:.12g}"


def _resample_component(
    source_samples: list[ResponseSample],
    target_frequency_hz: float,
) -> tuple[complex, float] | None:
    frequencies = [sample.frequency_hz for sample in source_samples]
    right_index = bisect.bisect_left(frequencies, target_frequency_hz)
    if right_index < len(source_samples) and math.isclose(
        frequencies[right_index],
        target_frequency_hz,
        rel_tol=1.0e-12,
        abs_tol=0.0,
    ):
        exact = source_samples[right_index]
        component = next(iter(exact.active_components))
        return exact.values[component], exact.standard_errors[component]
    if right_index == 0 or right_index == len(source_samples):
        return None
    left = source_samples[right_index - 1]
    right = source_samples[right_index]
    component = next(iter(left.active_components))
    fraction = (
        (math.log(target_frequency_hz) - math.log(left.frequency_hz))
        / (math.log(right.frequency_hz) - math.log(left.frequency_hz))
    )
    left_value = left.values[component]
    right_value = right.values[component]
    left_error = left.standard_errors[component]
    right_error = right.standard_errors[component]
    if not all(
        math.isfinite(value)
        for value in (
            left_value.real,
            left_value.imag,
            right_value.real,
            right_value.imag,
            left_error,
            right_error,
        )
    ):
        raise ValueError(f"Cannot interpolate non-finite {component} data")
    if left_error < 0.0 or right_error < 0.0:
        raise ValueError(f"Cannot interpolate negative {component} errors")
    return (
        left_value * (1.0 - fraction) + right_value * fraction,
        left_error * (1.0 - fraction) + right_error * fraction,
    )


def _resample_sample(
    source_samples: list[ResponseSample],
    target_frequency_hz: float,
) -> ResponseSample | None:
    values: dict[str, complex] = {}
    errors: dict[str, float] = {}
    components = frozenset().union(
        *(sample.active_components for sample in source_samples)
    )
    for component in components:
        component_samples = [
            ResponseSample(
                sample.frequency_hz,
                {component: sample.values[component]},
                {component: sample.standard_errors[component]},
                frozenset({component}),
            )
            for sample in source_samples
            if component in sample.active_components
        ]
        result = _resample_component(component_samples, target_frequency_hz)
        if result is not None:
            values[component], errors[component] = result
    if not values:
        return None
    return ResponseSample(
        frequency_hz=target_frequency_hz,
        values=values,
        standard_errors=errors,
        active_components=frozenset(values),
    )


def resample_survey_log_frequency(
    survey: Survey,
    target_frequencies_hz: tuple[float, ...],
) -> Survey:
    """Resample each station without extrapolation on a log-frequency axis."""

    if not target_frequencies_hz:
        raise ValueError("Target frequencies must be nonempty")
    if any(
        not math.isfinite(frequency) or frequency <= 0.0
        for frequency in target_frequencies_hz
    ):
        raise ValueError("Target frequencies must be finite and positive")
    if len(set(target_frequencies_hz)) != len(target_frequencies_hz):
        raise ValueError("Target frequencies must be unique")

    matching_counts = {frequency: 0 for frequency in target_frequencies_hz}
    stations = []
    for station in survey.stations:
        source_samples = sorted(station.samples, key=lambda sample: sample.frequency_hz)
        selected: list[ResponseSample] = []
        for target in target_frequencies_hz:
            sample = _resample_sample(source_samples, target)
            if sample is not None:
                selected.append(sample)
                matching_counts[target] += 1
        stations.append(replace(station, samples=tuple(selected)))

    metadata = dict(survey.metadata)
    metadata["resampling"] = {
        "method": "linear complex components and standard errors in log(frequency)",
        "target_frequencies_hz": list(target_frequencies_hz),
        "matching_station_counts": {
            _frequency_key(frequency): matching_counts[frequency]
            for frequency in target_frequencies_hz
        },
        "extrapolation": "disabled",
    }
    return replace(survey, stations=tuple(stations), metadata=metadata)
