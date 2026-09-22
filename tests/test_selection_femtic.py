from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import mt2femtic.selection as selection_module
from mt2femtic.config import ObservationRefinementConfig, SelectionConfig
from mt2femtic.femtic_writer import validate_femtic_inputs, write_femtic_inputs
from mt2femtic.model import ResponseSample, Station, Survey
from mt2femtic.selection import apply_impedance_floor, select_survey


def sample_survey(
    *,
    north_m: float = 2000.0,
    east_m: float = 3000.0,
    frequency_hz: float = 0.10005,
) -> Survey:
    sample = ResponseSample(
        frequency_hz=frequency_hz,
        values={
            "ZXY": 3.0 + 4.0j,
            "ZYX": -6.0 - 8.0j,
            "TX": 0.1 + 0.2j,
        },
        standard_errors={"ZXY": 0.001, "ZYX": 0.002, "TX": 0.001},
        active_components=frozenset({"ZXY", "ZYX", "TX"}),
    )
    station = Station(
        station_id=1,
        name="S01",
        longitude_deg=9.0,
        latitude_deg=50.0,
        elevation_m=750.0,
        north_m=north_m,
        east_m=east_m,
        model_x_km=north_m / 1000.0,
        model_y_km=east_m / 1000.0,
        surface_depth_km=0.25,
        samples=(sample,),
    )
    return Survey((station,), "ohm", "exp_minus_iwt", "test")


def selection_config(
    *,
    periods: tuple[float, ...] = (10.0,),
    rtol: float = 1.0e-3,
    policy: str = "exact",
) -> SelectionConfig:
    return SelectionConfig(
        periods_s=periods,
        relative_tolerance=rtol,
        impedance_error_floor_fraction=0.01,
        vtf_error_floor_absolute=0.01,
        max_vtf_period_s=10000.0,
        frequency_policy=policy,
    )


class SelectionFemticTests(unittest.TestCase):
    def _policy_function(self):
        self.assertTrue(hasattr(selection_module, "prepare_selected_survey"))
        return selection_module.prepare_selected_survey

    def test_exact_policy_preserves_matched_source_frequency(self) -> None:
        selected = self._policy_function()(
            sample_survey(), selection_config(policy="exact")
        )
        self.assertEqual(selected.stations[0].samples[0].frequency_hz, 0.10005)
        self.assertEqual(selected.metadata["selection"]["frequency_policy"], "exact")  # type: ignore[index]

    def test_log_linear_policy_interpolates_without_extrapolation(self) -> None:
        source = sample_survey(frequency_hz=0.01)
        first_station = source.stations[0]
        low = replace(
            first_station.samples[0],
            frequency_hz=0.01,
            values={"ZXY": 1.0 + 1.0j, "ZYX": 4.0 + 4.0j},
            standard_errors={"ZXY": 0.1, "ZYX": 0.2},
            active_components=frozenset({"ZXY", "ZYX"}),
        )
        high = replace(
            low,
            frequency_hz=1.0,
            values={"ZXY": 3.0 + 3.0j, "ZYX": 16.0 + 16.0j},
            standard_errors={"ZXY": 0.3, "ZYX": 0.4},
        )
        source = replace(
            source,
            stations=(replace(first_station, samples=(low, high)),),
        )
        selected = self._policy_function()(
            source, selection_config(periods=(10.0,), policy="log_linear")
        )
        sample = selected.stations[0].samples[0]
        self.assertEqual(sample.frequency_hz, 0.1)
        self.assertAlmostEqual(sample.values["ZXY"].real, 2.0)
        self.assertAlmostEqual(sample.values["ZYX"].real, 10.0)
        self.assertEqual(
            selected.metadata["selection"]["frequency_policy"], "log_linear"  # type: ignore[index]
        )
        with self.assertRaisesRegex(ValueError, "Requested period 1000 s is unavailable"):
            self._policy_function()(
                source,
                selection_config(periods=(1000.0,), policy="log_linear"),
            )

    def test_frequency_match_uses_declared_relative_tolerance(self) -> None:
        selected = select_survey(sample_survey(), selection_config())
        self.assertEqual(selected.stations[0].samples[0].frequency_hz, 0.10005)

    def test_unresolved_period_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "Requested period 100 s is unavailable"):
            select_survey(sample_survey(), selection_config(periods=(100.0,)))

    def test_impedance_floor_matches_validated_fortran_rule(self) -> None:
        sample = ResponseSample(
            frequency_hz=1.0,
            values={"ZXX": 1.0 + 0.0j, "ZXY": 4.0 + 0.0j, "ZYX": 9.0 + 0.0j},
            standard_errors={"ZXX": 0.001, "ZXY": 0.001, "ZYX": 0.001},
            active_components=frozenset({"ZXX", "ZXY", "ZYX"}),
        )
        floored = apply_impedance_floor(sample, 0.01)
        self.assertAlmostEqual(floored.standard_errors["ZXX"], 0.06)
        self.assertAlmostEqual(floored.standard_errors["ZXY"], 0.06)

    def test_writer_uses_model_x_and_model_y(self) -> None:
        refinement = ObservationRefinementConfig(
            radius_km=50.0, level=24, weight=0.5
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_femtic_inputs(
                sample_survey(), Path(temporary), refinement
            )
            lines = paths["observe"].read_text(encoding="ascii").splitlines()
            self.assertEqual(lines[1], "1 1001 0 2 3")
            vtf_index = lines.index("VTF 1")
            self.assertEqual(lines[vtf_index + 1], "1001 1001 1 2 3")
            summary = validate_femtic_inputs(paths)
            self.assertEqual(summary["mt_station_count"], 1)
            self.assertEqual(summary["mt_sample_count"], 1)
            self.assertEqual(summary["vtf_sample_count"], 1)

    def test_missing_components_use_negative_error_markers(self) -> None:
        refinement = ObservationRefinementConfig(
            radius_km=50.0, level=24, weight=0.5
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = write_femtic_inputs(
                sample_survey(), Path(temporary), refinement
            )
            lines = paths["observe"].read_text(encoding="ascii").splitlines()
            mt_data = [float(value) for value in lines[3].split()]
            self.assertEqual(mt_data[1:3], [0.0, 0.0])
            self.assertEqual(mt_data[9:11], [-1.0, -1.0])


if __name__ == "__main__":
    unittest.main()
