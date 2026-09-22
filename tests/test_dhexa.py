from __future__ import annotations

import hashlib
from dataclasses import replace
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mt2femtic.config import (
    AirLayerConfig,
    ConfiguredAxesConfig,
    GeneratorConfig,
    MeshConfig,
    MeshTopographyConfig,
)
from mt2femtic.dhexa import (
    GeneratorIdentity,
    build_configured_axes,
    build_generator_command,
    build_modem_axes,
    run_generator,
    validate_dhexa_products,
    validate_station_mesh_bounds,
    verify_model_round_trip,
    verify_generator,
    write_source_model_block,
    write_meshgen_input,
)
from mt2femtic.model import ModemModel, Station


def axes_config() -> ConfiguredAxesConfig:
    return ConfiguredAxesConfig(
        x_max_km=5.0,
        x_uniform_limit_km=2.0,
        x_spacing_km=1.0,
        x_padding_factor=1.5,
        y_max_km=5.0,
        y_uniform_limit_km=2.0,
        y_spacing_km=1.0,
        y_padding_factor=1.5,
        z_max_km=8.0,
        z_spacing_km=0.5,
        z_padding_factor=1.5,
    )


def mesh_config(
    mode: str = "configured_axes", air_scheme: str = "geometric"
) -> MeshConfig:
    return MeshConfig(
        backend="dhexa",
        geometry_mode=mode,
        configured_axes=axes_config() if mode == "configured_axes" else None,
        modem_model_path=(
            None if mode == "configured_axes" else Path("model.rho")
        ),
        observation_refinement="from_data",
        air_layers=AirLayerConfig(
            scheme=air_scheme,
            count=12 if air_scheme == "modem_fixed_height" else 2,
            top_km=1000.0 if air_scheme == "modem_fixed_height" else 10.0,
            bottom_km=None if air_scheme == "modem_fixed_height" else 1.0,
        ),
        initial_resistivity_ohm_m=100.0,
        air_resistivity_ohm_m=1.0e10,
        sea_resistivity_ohm_m=0.3,
        model_round_trip_tolerance=1.0e-12,
        station_coordinate_tolerance_km=1.0e-9,
    )


def topography_config(enabled: bool = False) -> MeshTopographyConfig:
    return MeshTopographyConfig(
        mode="native" if enabled else "flat",
        path=None,
        max_interpolation_points=3,
        search_radius_km=100.0,
        tolerance_km=0.001,
    )


def modem_model(rotation_deg: float = 0.0) -> ModemModel:
    return ModemModel(
        dimensions=(2, 1, 2),
        north_widths_m=(1000.0, 2000.0),
        east_widths_m=(3000.0,),
        depth_widths_m=(500.0, 1000.0),
        resistivity_ohm_m=(100.0, 200.0, 300.0, 400.0),
        origin_m=(-1000.0, -1500.0, 0.0),
        rotation_deg=rotation_deg,
        representation="LINEAR",
    )


def station(x_km: float, y_km: float) -> Station:
    return Station(1, "S01", None, None, 0.0, 0.0, 0.0, x_km, y_km, 0.0, ())


def write_fake_products(root: Path, mesh_type: str = "DHEXA") -> None:
    points = [
        (-1000, -500, 0), (0, -500, 0), (0, 500, 0), (-1000, 500, 0),
        (-1000, -500, 500), (0, -500, 500), (0, 500, 500), (-1000, 500, 500),
        (0, -500, 0), (1000, -500, 0), (1000, 500, 0), (0, 500, 0),
        (0, -500, 500), (1000, -500, 500), (1000, 500, 500), (0, 500, 500),
    ]
    mesh_lines = [mesh_type, str(len(points))]
    mesh_lines.extend(
        f"{index} {x} {y} {z}" for index, (x, y, z) in enumerate(points)
    )
    mesh_lines.append("2")
    (root / "mesh.dat").write_text("\n".join(mesh_lines) + "\n", encoding="ascii")
    vtk_lines = [
        "# vtk DataFile Version 2.0",
        "MeshData",
        "ASCII",
        "DATASET UNSTRUCTURED_GRID",
        "POINTS 16 float",
    ]
    vtk_lines.extend(f"{x} {y} {z}" for x, y, z in points)
    vtk_lines.extend(
        (
            "CELLS 2 18",
            "8 0 1 2 3 4 5 6 7",
            "8 8 9 10 11 12 13 14 15",
            "CELL_TYPES 2",
            "12",
            "12",
            "CELL_DATA 2",
            "SCALARS BlockID int",
            "LOOKUP_TABLE default",
            "1",
            "2",
        )
    )
    (root / "MeshData.vtk").write_text("\n".join(vtk_lines) + "\n", encoding="ascii")
    block_lines = [
        "2 3",
        "0 1",
        "1 2",
        "0 1.0e10 1.0e-20 1.0e20 1.0 1",
        "1 50 1.0e-20 1.0e20 1.0 0",
        "2 50 1.0e-20 1.0e20 1.0 0",
    ]
    (root / "resistivity_block_iter0.dat").write_text(
        "\n".join(block_lines) + "\n",
        encoding="ascii",
    )


class DHexaTests(unittest.TestCase):
    def test_configured_axes_are_strictly_increasing(self) -> None:
        axes = build_configured_axes(mesh_config())
        for values in (axes.x_km, axes.y_km, axes.z_km):
            self.assertTrue(all(left < right for left, right in zip(values, values[1:])))
        self.assertEqual(axes.z_km[:2], (-10.0, -1.0))

    def test_modem_axes_preserve_declared_origin(self) -> None:
        axes = build_modem_axes(modem_model(), mesh_config("modem_model_axes"))
        self.assertEqual(axes.x_km, (-1.0, 0.0, 2.0))
        self.assertEqual(axes.y_km, (-1.5, 1.5))
        self.assertEqual(axes.z_km, (-10.0, -1.0, 0.0, 0.5, 1.5))

    def test_modem_fixed_height_air_matches_reference_boundaries(self) -> None:
        axes = build_modem_axes(
            modem_model(), mesh_config("modem_model_axes", "modem_fixed_height")
        )
        air = axes.z_km[:12]
        self.assertEqual(len(air), 12)
        self.assertAlmostEqual(air[0], -999.5, places=9)
        self.assertAlmostEqual(air[-1], -0.44201070319762914, places=9)
        self.assertTrue(all(left < right for left, right in zip(air, air[1:])))

    def test_geometric_air_matches_broken_hill_r5b_boundaries(self) -> None:
        config = mesh_config("modem_model_axes")
        config = replace(
            config,
            air_layers=AirLayerConfig(
                scheme="geometric",
                count=7,
                top_km=150.0,
                bottom_km=0.25,
            ),
        )
        air = build_modem_axes(modem_model(), config).z_km[:7]
        self.assertEqual(
            tuple(f"{value:.12f}" for value in air),
            (
                "-150.000000000000",
                "-51.649491559623",
                "-17.784466522450",
                "-6.123724356958",
                "-2.108581663254",
                "-0.726047805460",
                "-0.250000000000",
            ),
        )

    def test_rotated_modem_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonzero ModEM model rotation"):
            build_modem_axes(modem_model(10.0), mesh_config("modem_model_axes"))

    def test_station_outside_mesh_is_rejected(self) -> None:
        axes = build_configured_axes(mesh_config())
        with self.assertRaisesRegex(ValueError, "Station S01 lies outside"):
            validate_station_mesh_bounds((station(20.0, 0.0),), axes)

    def test_meshgen_input_contains_only_3d_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "meshgen.inp"
            write_meshgen_input(
                build_configured_axes(mesh_config()),
                mesh_config(),
                topography_config(False),
                path,
                None,
            )
            text = path.read_text(encoding="ascii")
            self.assertIn("DIVISION_NUMBERS\n", text)
            self.assertIn("INITIAL_RESISTIVITY\n", text)
            self.assertNotIn("MT4D", text)
            self.assertNotIn("TOPO\n", text)
            self.assertTrue(text.endswith("END\n"))

    def test_meshgen_topography_parameters_follow_dhexa_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            topography_path = root / "topography.dat"
            topography_path.write_text("0 0 0\n", encoding="ascii")
            path = root / "meshgen.inp"
            write_meshgen_input(
                build_configured_axes(mesh_config()),
                mesh_config(),
                topography_config(True),
                path,
                topography_path,
            )
            text = path.read_text(encoding="ascii")
            self.assertIn("TOPO\ntopography.dat\n3\n100\n0.001\n", text)

    def test_rejects_tetrahedral_mesh_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_fake_products(root, mesh_type="TETRA")
            with self.assertRaisesRegex(ValueError, "Expected DHEXA mesh"):
                validate_dhexa_products(root)

    def test_validates_dhexa_product_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_fake_products(root)
            summary = validate_dhexa_products(root)
            self.assertEqual(summary.node_count, 16)
            self.assertEqual(summary.element_count, 2)
            self.assertEqual(summary.parameter_count, 3)

    def test_model_order_round_trip_is_exact_for_numbered_fixture(self) -> None:
        source = ModemModel(
            dimensions=(2, 1, 1),
            north_widths_m=(1000.0, 1000.0),
            east_widths_m=(1000.0,),
            depth_widths_m=(500.0,),
            resistivity_ohm_m=(100.0, 200.0),
            origin_m=(-1000.0, -500.0, 0.0),
            rotation_deg=0.0,
            representation="LINEAR",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_fake_products(root)
            output = root / "resistivity_block_source_model.dat"
            mapping = write_source_model_block(source, root, output)
            self.assertEqual(mapping["mapped_parameter_count"], 2)
            self.assertEqual(verify_model_round_trip(source, root, output, 1.0e-12), 0.0)
            block_rows = output.read_text(encoding="ascii").splitlines()[-3:]
            self.assertEqual(float(block_rows[1].split()[1]), 200.0)
            self.assertEqual(float(block_rows[2].split()[1]), 100.0)

    def test_wrong_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "makeDHexaMesh"
            executable.write_bytes(b"fake")
            config = GeneratorConfig(executable, "v1.6.1", "0" * 64, 10)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_generator(config)

    @patch("mt2femtic.dhexa.Path.resolve", autospec=True, side_effect=lambda path: path)
    def test_linux_binary_uses_wsl_on_windows(self, _resolve_mock) -> None:
        # Preserve the absolute Windows fixture paths on non-Windows test hosts.
        command = build_generator_command(
            Path("D:/tools/makeDHexaMesh"),
            Path("C:/runs/case"),
            platform="windows",
            executable_is_elf=True,
        )
        self.assertEqual(
            command,
            ["wsl.exe", "--cd", "/mnt/c/runs/case", "/mnt/d/tools/makeDHexaMesh"],
        )

    @patch("mt2femtic.dhexa.subprocess.run")
    def test_nonzero_exit_is_rejected(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(["fake"], 7, "started", "failed")
        with tempfile.TemporaryDirectory() as temporary:
            identity = GeneratorIdentity(Path(sys.executable), "v1.6.1", "a" * 64, 10)
            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                run_generator(identity, Path(temporary))

    @patch("mt2femtic.dhexa.subprocess.run")
    def test_successful_run_requires_version_and_end_markers(self, run_mock) -> None:
        stdout = "Start Mesh Generation. Version v1.6.1\nEnd Mesh Generation.\n"
        run_mock.return_value = subprocess.CompletedProcess(["fake"], 0, stdout, "")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = GeneratorIdentity(Path(sys.executable), "v1.6.1", "a" * 64, 10)
            result = run_generator(identity, root)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root / "meshgen.stdout").read_text(encoding="utf-8"), stdout)


if __name__ == "__main__":
    unittest.main()
