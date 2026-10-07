from __future__ import annotations

import unittest

from mt2femtic.model import ResponseSample, Station, Survey
from mt2femtic.resampling import resample_survey_log_frequency


def sample(
    frequency_hz: float,
    zxy: complex,
    zyx: complex,
    error: float,
    *,
    tx: complex | None = None,
) -> ResponseSample:
    values = {"ZXY": zxy, "ZYX": zyx}
    errors = {"ZXY": error, "ZYX": error}
    active = {"ZXY", "ZYX"}
    if tx is not None:
        values["TX"] = tx
        errors["TX"] = error
        active.add("TX")
    return ResponseSample(frequency_hz, values, errors, frozenset(active))


def survey() -> Survey:
    station = Station(
        station_id=1,
        name="S01",
        longitude_deg=None,
        latitude_deg=None,
        elevation_m=0.0,
        north_m=0.0,
        east_m=0.0,
        model_x_km=0.0,
        model_y_km=0.0,
        surface_depth_km=0.0,
        samples=(
            sample(1.0, 1.0 + 2.0j, -1.0 - 2.0j, 1.0, tx=0.1 + 0.2j),
            sample(100.0, 3.0 + 6.0j, -3.0 - 6.0j, 3.0),
        ),
    )
    return Survey((station,), "ohm", "exp_minus_iwt", "edi")


class ResamplingTests(unittest.TestCase):
    def test_interpolates_complex_values_and_errors_in_log_frequency(self) -> None:
        result = resample_survey_log_frequency(survey(), (10.0,))
        interpolated = result.stations[0].samples[0]
        self.assertEqual(interpolated.frequency_hz, 10.0)
        self.assertEqual(interpolated.values["ZXY"], 2.0 + 4.0j)
        self.assertEqual(interpolated.values["ZYX"], -2.0 - 4.0j)
        self.assertEqual(interpolated.standard_errors["ZXY"], 2.0)
        self.assertNotIn("TX", interpolated.active_components)

    def test_preserves_exact_sample_and_omits_extrapolation(self) -> None:
        original = survey().stations[0].samples[0]
        result = resample_survey_log_frequency(survey(), (1.0, 0.1, 1000.0))
        self.assertEqual(result.stations[0].samples, (original,))
        self.assertEqual(
            result.metadata["resampling"]["matching_station_counts"],
            {"1": 1, "0.1": 0, "1000": 0},
        )

    def test_interpolates_each_component_across_inactive_source_sample(self) -> None:
        source = survey()
        station = source.stations[0]
        inactive_z = ResponseSample(
            10.0,
            {"TX": 0.5 + 0.25j},
            {"TX": 0.2},
            frozenset({"TX"}),
        )
        source = Survey(
            (
                Station(
                    **{
                        **station.__dict__,
                        "samples": (
                            station.samples[0],
                            inactive_z,
                            station.samples[1],
                        ),
                    }
                ),
            ),
            source.impedance_unit,
            source.time_convention,
            source.source_type,
        )
        result = resample_survey_log_frequency(source, (10.0,))
        interpolated = result.stations[0].samples[0]
        self.assertEqual(interpolated.values["ZXY"], 2.0 + 4.0j)
        self.assertEqual(interpolated.values["TX"], 0.5 + 0.25j)

    def test_rejects_duplicate_or_nonpositive_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            resample_survey_log_frequency(survey(), (1.0, 1.0))
        with self.assertRaisesRegex(ValueError, "positive"):
            resample_survey_log_frequency(survey(), (0.0,))


if __name__ == "__main__":
    unittest.main()
