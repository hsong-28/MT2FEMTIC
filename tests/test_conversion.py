"""Known-value tests, independent NetCDF schema checks, and observation round trips."""

import contextlib
import importlib.util
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mt2femtic.cli import main
from mt2femtic.conversion import convert, read_observations


def write_modem(path, *, missing=False, sign="+", two_stations=True):
    lines = []
    for kind, components, unit in (
        ("Full_Impedance", ("ZXX", "ZXY", "ZYX", "ZYY"), "[mV/km]/[nT]"),
        ("Full_Vertical_Components", ("TX", "TY"), "[]"),
    ):
        lines.extend(["# Test fixture", "# Columns follow the ModEM list format",
                      f"> {kind}", rf"> exp({sign}i\omega t)", f"> {unit}", "> 0", "> -32 142", f"> 2 {2 if two_stations else 1}"])
        for k, name in enumerate(("S02", "S01") if two_stations else ("S02",)):
            for period in (0.5, 8.0):
                for j, component in enumerate(components):
                    if missing and name == "S02" and component == "TY" and period == 8:
                        continue
                    lines.append(f"{period} {name} -32 142 {1250 + k} {-2500 - k} {-400 + k} {component} {j + 1} {j + 2} {0.1 * (j + 1)}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.dat"
        write_modem(self.source)

    def assert_observations_equal(self, first, second):
        self.assertEqual(len(first.stations), len(second.stations))
        for a, b in zip(first.stations, second.stations):
            for attribute in ("station_id", "name", "latitude_deg", "longitude_deg", "surface_depth_km", "model_x_km", "model_y_km"):
                self.assertEqual(getattr(a, attribute), getattr(b, attribute))
            a_rows = sorted(a.samples, key=lambda r: r.frequency_hz)
            b_rows = sorted(b.samples, key=lambda r: r.frequency_hz)
            self.assertEqual(len(a_rows), len(b_rows))
            for x, y in zip(a_rows, b_rows):
                self.assertTrue(np.isclose(x.frequency_hz, y.frequency_hz, rtol=2e-14, atol=0))
                self.assertEqual(x.active_components, y.active_components)
                for c in x.active_components:
                    self.assertTrue(np.isclose(x.values[c], y.values[c], rtol=6e-12, atol=0))
                    self.assertTrue(np.isclose(x.standard_errors[c], y.standard_errors[c], rtol=6e-12, atol=0))

    def test_modem_known_units_sign_axes_and_nonzero_depth(self):
        source = read_observations(self.source, "modem")
        station = source.stations[0]
        self.assertEqual((station.model_x_km, station.model_y_km, station.surface_depth_km), (1.25, -2.5, -0.4))
        row = station.samples[0]
        self.assertAlmostEqual(row.values["ZXY"], (2 - 3j) * 4e-4 * math.pi)
        self.assertAlmostEqual(row.standard_errors["ZXY"], 0.2 * 4e-4 * math.pi)
        self.assertEqual(row.values["TX"], 1 - 2j)
        write_modem(self.source, sign="-")
        self.assertEqual(read_observations(self.source, "modem").stations[0].samples[0].values["TX"], 1 + 2j)

    @unittest.skipUnless(importlib.util.find_spec("netCDF4"), "optional conversion dependency")
    def test_bundled_complete_example_matches_halfspace_and_converts(self):
        source = Path(__file__).resolve().parents[1] / "examples/minimal/input/modem/complete.dat"
        convert(source, self.root / "j", "modem", "jif3d")
        convert(self.root / "j", self.root / "f", "jif3d", "femtic")
        row = read_observations(self.root / "f", "femtic").stations[0].samples[0]
        self.assertAlmostEqual(row.values["ZXY"], complex(0, -2 * math.pi * 4e-7 * math.pi * 100) ** 0.5)
        self.assertEqual(row.values["TX"], 0j)

    def test_modem_femtic_round_trip_preserves_missing_and_metadata(self):
        write_modem(self.source, missing=True)
        original = read_observations(self.source, "modem")
        convert(self.source, self.root / "f", "modem", "femtic")
        convert(self.root / "f", self.root / "m", "femtic", "modem")
        self.assert_observations_equal(original, read_observations(self.root / "m", "modem"))
        self.assertIn("-1 -1", (self.root / "f" / "observe.dat").read_text())

    def test_femtic_requires_explicit_depth_when_metadata_is_absent(self):
        convert(self.source, self.root / "f", "modem", "femtic")
        (self.root / "f" / "conversion.json").unlink()
        with self.assertRaisesRegex(ValueError, "no depths"):
            convert(self.root / "f", self.root / "bad", "femtic", "modem")
        convert(self.root / "f", self.root / "m", "femtic", "modem", surface_depth_m=-150)
        self.assertEqual(read_observations(self.root / "m", "modem").stations[0].surface_depth_km, -0.15)
        self.assertFalse((self.root / "bad").exists())

    def test_stale_metadata_overwrite_rotation_and_remote_reference_fail(self):
        convert(self.source, self.root / "f", "modem", "femtic")
        with self.assertRaisesRegex(ValueError, "already exists"):
            convert(self.source, self.root / "f", "modem", "femtic")
        observe = self.root / "f" / "observe.dat"
        observe.write_text(observe.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "metadata"):
            read_observations(self.root / "f", "femtic")
        self.source.write_text(self.source.read_text().replace("> 0\n", "> 30\n"))
        with self.assertRaisesRegex(ValueError, "orientation"):
            convert(self.source, self.root / "bad", "modem", "femtic")
        (self.root / "f" / "conversion.json").unlink()
        observe.write_text(observe.read_text().replace("1001 1001 1 1.25", "1001 1001 1 10.25"))
        with self.assertRaisesRegex(ValueError, "co-located"):
            read_observations(observe, "femtic")

    def test_cli_failure_is_nonzero_and_does_not_leave_output(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["convert", "--from", "modem", "--to", "femtic", "--input", str(self.root / "absent"), "--output", str(self.root / "bad")])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "bad").exists())

    def test_tip_only_station_and_different_frequency_inventory_are_retained(self):
        lines = self.source.read_text().splitlines()
        # Remove impedance for S02 only, keeping both tipper stations.
        lines = [line for line in lines if not (" S02 " in line and " Z" in line)]
        first_count = lines.index("> 2 2")
        lines[first_count] = "> 2 1"
        self.source.write_text("\n".join(lines) + "\n")
        original = read_observations(self.source, "modem")
        convert(self.source, self.root / "f", "modem", "femtic")
        convert(self.root / "f", self.root / "m", "femtic", "modem")
        self.assert_observations_equal(original, read_observations(self.root / "m", "modem"))

    def test_modem_wrong_counts_and_mixed_time_conventions_fail(self):
        self.source.write_text(self.source.read_text().replace("> 2 2", "> 3 2", 1))
        with self.assertRaisesRegex(ValueError, "counts"):
            read_observations(self.source, "modem")
        write_modem(self.source)
        self.source.write_text(self.source.read_text().replace("exp(+i", "exp(-i", 1))
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            read_observations(self.source, "modem")

    def test_broken_hill_full_observation_round_trip(self):
        source = Path(__file__).resolve().parents[1] / "examples/broken_hill/input/modem/BH_31.dat"
        original = read_observations(source, "modem")
        payload = convert(source, self.root / "f", "modem", "femtic")
        self.assertEqual(payload["complex_observation_count"], 3006)
        self.assertEqual(len(payload["stations"]), 21)
        convert(self.root / "f", self.root / "m", "femtic", "modem")
        self.assert_observations_equal(original, read_observations(self.root / "m", "modem"))

    @unittest.skipUnless(importlib.util.find_spec("netCDF4"), "optional conversion dependency")
    def test_all_six_directions_and_raw_jif3d_schema(self):
        from netCDF4 import Dataset
        original = read_observations(self.source, "modem")
        convert(self.source, self.root / "j", "modem", "jif3d")
        # Independently inspect the native schema used by Jif3D MTData/TipperData readers.
        with Dataset(self.root / "j/survey.nc") as nc:
            self.assertEqual(nc["Zxy_re"].dimensions, ("Frequency", "StationNumber"))
            self.assertEqual(nc["Zxy_re"].units, "Ohm")
            self.assertEqual(nc["MeasPosX"][0], 1250)
            self.assertEqual(nc["MeasPosY"][0], -2500)
            self.assertEqual(nc["MeasPosZ"][0], -400)
            self.assertAlmostEqual(nc["Zxy_im"][0, 0], 3 * 4e-4 * math.pi)
            self.assertEqual(nc["Tx_im"][0, 0], 2)
            np.testing.assert_array_equal(nc["HIndices"][:], [[0, 1], [0, 1]])
        convert(self.root / "j", self.root / "jf", "jif3d", "femtic")
        convert(self.root / "j", self.root / "jm", "jif3d", "modem")
        convert(self.root / "jf", self.root / "fj", "femtic", "jif3d")
        for path, format in (("jf", "femtic"), ("jm", "modem"), ("fj", "jif3d")):
            self.assert_observations_equal(original, read_observations(self.root / path, format))

    @unittest.skipUnless(importlib.util.find_spec("netCDF4"), "optional conversion dependency")
    def test_upstream_jif3d_example_and_all_six_directions(self):
        fixture = Path(__file__).parent / "fixtures/jif3d"
        # Independent oracle: the upstream MTT records have 23 columns.
        mtt = np.fromstring((fixture / "testJ.mtt").read_text(), sep=" ").reshape(-1, 23)
        self.assertEqual(mtt.shape, (5, 23))
        for kind, components, start, error_start, scale in (
            ("impedance", ("ZXX", "ZXY", "ZYX", "ZYY"), 2, 10, 4e-4 * math.pi),
            ("tipper", ("TX", "TY"), 14, 18, 1.0),
        ):
            source = fixture / (kind + ".nc")
            original = read_observations(source, "jif3d")
            self.assertEqual(len(original.stations), 1)
            station = original.stations[0]
            self.assertEqual(station.name, "go_01")
            self.assertEqual((station.model_x_km, station.model_y_km, station.surface_depth_km), (0, 0, -0.119))
            self.assertEqual(len(station.samples), 5)
            for sample, row in zip(station.samples, mtt, strict=True):
                self.assertEqual(sample.frequency_hz, row[0])
                self.assertEqual(sample.active_components, frozenset(components))
                for i, c in enumerate(components):
                    value = complex(row[start + 2*i], -row[start + 2*i + 1]) * scale
                    np.testing.assert_allclose(sample.values[c], value, rtol=2e-14, atol=0)
                    np.testing.assert_allclose(sample.standard_errors[c], abs(row[error_start + i]) * scale, rtol=2e-14, atol=0)
            for route in (("femtic", "modem", "jif3d"), ("modem", "femtic", "jif3d")):
                current, format = source, "jif3d"
                for step, target in enumerate(route):
                    with self.subTest(kind=kind, route=route, step=step):
                        output = self.root / (kind + "-" + "-".join(route)) / str(step)
                        convert(current, output, format, target)
                        self.assert_observations_equal(original, read_observations(output, target))
                        current, format = output, target

    @unittest.skipUnless(importlib.util.find_spec("netCDF4"), "optional conversion dependency")
    def test_jif3d_missing_grid_and_nonidentity_metadata_rejected(self):
        from netCDF4 import Dataset
        write_modem(self.source, missing=True)
        with self.assertRaisesRegex(ValueError, "complete common frequency grid"):
            convert(self.source, self.root / "bad", "modem", "jif3d")
        self.assertFalse((self.root / "bad").exists())
        write_modem(self.source)
        convert(self.source, self.root / "j", "modem", "jif3d")
        (self.root / "j/conversion.json").unlink()
        with Dataset(self.root / "j/survey.nc", "a") as nc:
            nc["ExIndices"][0, 0] = 1
        with self.assertRaisesRegex(ValueError, "co-located"):
            read_observations(self.root / "j", "jif3d")

    @unittest.skipUnless(importlib.util.find_spec("netCDF4"), "optional conversion dependency")
    def test_independent_native_halfspace_fixture(self):
        from netCDF4 import Dataset
        path = self.root / "native.nc"
        # Jif3D MTEquations::ImpedanceHalfspace: sqrt(i * omega * mu0 / sigma).
        z = complex(0, 2 * math.pi * 4e-7 * math.pi / 0.01) ** 0.5
        with Dataset(path, "w") as nc:
            nc.createDimension("Frequency", 1)
            nc.createDimension("StationNumber", 1)
            nc.createVariable("Frequency", "f8", ("Frequency",))[:] = [1]
            for axis, value in zip("XYZ", (120, -340, -560)):
                nc.createVariable("MeasPos" + axis, "f8", ("StationNumber",))[:] = [value]
            for c, value in (("Zxx", 0j), ("Zxy", z), ("Zyx", -z), ("Zyy", 0j)):
                for suffix, scalar in (("_re", value.real), ("_im", value.imag)):
                    nc.createVariable(c + suffix, "f8", ("Frequency", "StationNumber"))[:] = [[scalar]]
                nc.createVariable("d" + c, "f8", ("Frequency", "StationNumber"))[:] = [[0.001]]
        survey = read_observations(path, "jif3d")
        self.assertEqual(survey.stations[0].samples[0].values["ZXY"], z.conjugate())
        self.assertEqual(survey.stations[0].surface_depth_km, -0.56)
        convert(path, self.root / "f", "jif3d", "femtic")
        with Dataset(path, "a") as nc:
            nc["dZxy"][:] = -1
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            read_observations(path, "jif3d")


if __name__ == "__main__":
    unittest.main()
