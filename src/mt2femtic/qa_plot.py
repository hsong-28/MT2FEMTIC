"""Station and topography preprocessing figure."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .model import Station
from .topography import TopographySummary


def plot_station_topography(
    stations: Sequence[Station],
    topography: TopographySummary,
    output_path: Path,
    dataset_id: str,
    projected_crs: str,
    azimuth_deg: float,
) -> None:
    station_x = []
    station_y = []
    for station in stations:
        if station.model_x_km is None or station.model_y_km is None:
            raise ValueError(f"Station {station.name} has no normalized coordinates")
        station_x.append(station.model_x_km)
        station_y.append(station.model_y_km)

    figure, axis = plt.subplots(figsize=(7.0, 6.0), constrained_layout=True)
    if topography.enabled:
        terrain = axis.scatter(
            [point.model_y_km for point in topography.points],
            [point.model_x_km for point in topography.points],
            c=[point.elevation_m for point in topography.points],
            cmap="terrain",
            marker="s",
            s=28,
            linewidths=0,
            rasterized=True,
        )
        colorbar = figure.colorbar(terrain, ax=axis, pad=0.02)
        colorbar.set_label("Elevation (m)")
    else:
        axis.text(
            0.02,
            0.98,
            "Flat-earth configuration",
            transform=axis.transAxes,
            ha="left",
            va="top",
        )
    axis.scatter(
        station_y,
        station_x,
        marker="v",
        s=42,
        facecolors="white",
        edgecolors="black",
        linewidths=0.8,
        label="MT station",
    )
    axis.set_xlabel("FEMTIC Y east (km)")
    axis.set_ylabel("FEMTIC X north (km)")
    axis.set_aspect("equal", adjustable="box")
    axis.grid(True, color="0.85", linewidth=0.5)
    axis.legend(loc="best")
    axis.set_title(
        f"{dataset_id} | {projected_crs} | model azimuth {azimuth_deg:g} deg"
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=200)
    plt.close(figure)


def plot_period_coverage(
    stations: Sequence[Station],
    requested_periods_s: Sequence[float],
    output_path: Path,
    dataset_id: str,
) -> None:
    """Plot selected periods by station for a compact data-stage QA check."""

    if not stations:
        raise ValueError("Period-coverage plot requires at least one station")
    if not requested_periods_s or any(period <= 0.0 for period in requested_periods_s):
        raise ValueError("Requested periods must be positive")
    figure_height = max(3.5, min(10.0, 2.5 + 0.18 * len(stations)))
    figure, axis = plt.subplots(
        figsize=(8.0, figure_height), constrained_layout=True
    )
    for period in requested_periods_s:
        axis.axvline(period, color="0.82", linewidth=0.6, zorder=0)
    for row, station in enumerate(stations):
        periods = sorted({1.0 / sample.frequency_hz for sample in station.samples})
        if periods:
            axis.scatter(
                periods,
                [row] * len(periods),
                marker="|",
                s=90,
                linewidths=1.5,
                color="#1665a7",
            )
    axis.set_xscale("log")
    axis.set_xlabel("Period (s)")
    axis.set_ylabel("MT station")
    axis.set_yticks(range(len(stations)))
    axis.set_yticklabels([station.name for station in stations], fontsize=7)
    axis.set_ylim(len(stations) - 0.5, -0.5)
    axis.grid(True, axis="x", which="both", color="0.9", linewidth=0.5)
    axis.set_title(
        f"{dataset_id} | selected period coverage | {len(stations)} stations"
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=200)
    plt.close(figure)
