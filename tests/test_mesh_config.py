from __future__ import annotations

import unittest
from pathlib import Path

from mt2femtic.config import config_from_dict, config_to_dict


def configured_axes_payload() -> dict[str, object]:
    return {
        "x_max_km": 100.0,
        "x_uniform_limit_km": 20.0,
        "x_spacing_km": 1.0,
        "x_padding_factor": 1.3,
        "y_max_km": 100.0,
        "y_uniform_limit_km": 20.0,
        "y_spacing_km": 1.0,
        "y_padding_factor": 1.3,
        "z_max_km": 200.0,
        "z_spacing_km": 0.1,
        "z_padding_factor": 1.2,
    }


def valid_mesh_config_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "config_kind": "mesh",
        "mesh_id": "unit-mesh",
        "mesh": {
            "backend": "dhexa",
            "geometry_mode": "configured_axes",
            "configured_axes": configured_axes_payload(),
            "modem_model_path": None,
            "observation_refinement": "from_data",
            "air_layers": {
                "scheme": "geometric",
                "count": 4,
                "top_km": 100.0,
                "bottom_km": 0.25,
            },
            "initial_resistivity_ohm_m": 100.0,
            "air_resistivity_ohm_m": 1.0e10,
            "sea_resistivity_ohm_m": 0.3,
            "model_round_trip_tolerance": 1.0e-12,
            "station_coordinate_tolerance_km": 1.0e-9,
        },
        "topography": {
            "mode": "flat",
            "path": None,
            "max_interpolation_points": 3,
            "search_radius_km": 100.0,
            "tolerance_km": 0.001,
        },
        "generator": {
            "path": "bin/makeDHexaMesh",
            "version": "v1.6.1",
            "sha256": "0" * 64,
            "timeout_s": 600,
        },
        "run": {"overwrite": False, "resume": False},
    }


class MeshConfigTests(unittest.TestCase):
    def test_mesh_config_has_no_data_source_dependency(self) -> None:
        try:
            config = config_from_dict(
                valid_mesh_config_payload(), Path("C:/portable/configs")
            )
        except ValueError as exc:
            self.fail(str(exc))
        self.assertEqual(config.config_kind, "mesh")
        self.assertEqual(config.mesh_id, "unit-mesh")
        self.assertFalse(hasattr(config, "source"))
        self.assertFalse(hasattr(config, "selection"))
        self.assertEqual(config.mesh.observation_refinement, "from_data")

    def test_mesh_config_accepts_no_observation_refinement(self) -> None:
        payload = valid_mesh_config_payload()
        mesh = payload["mesh"]
        assert isinstance(mesh, dict)
        mesh["observation_refinement"] = "none"
        config = config_from_dict(payload)
        self.assertEqual(config.mesh.observation_refinement, "none")

    def test_mesh_config_rejects_unknown_observation_refinement(self) -> None:
        payload = valid_mesh_config_payload()
        mesh = payload["mesh"]
        assert isinstance(mesh, dict)
        mesh["observation_refinement"] = "automatic"
        with self.assertRaisesRegex(
            ValueError, "mesh.observation_refinement must be from_data or none"
        ):
            config_from_dict(payload)

    def test_mesh_paths_resolve_from_configuration_directory(self) -> None:
        config = config_from_dict(
            valid_mesh_config_payload(), Path("C:/portable/configs")
        )
        self.assertEqual(
            config.generator.path,
            Path("C:/portable/configs/bin/makeDHexaMesh").resolve(),
        )

    def test_mesh_config_rejects_data_source_key(self) -> None:
        payload = valid_mesh_config_payload()
        payload["source"] = {}
        with self.assertRaisesRegex(ValueError, "Unknown configuration key: source"):
            config_from_dict(payload)

    def test_modem_axes_requires_model_path(self) -> None:
        payload = valid_mesh_config_payload()
        mesh = payload["mesh"]
        assert isinstance(mesh, dict)
        mesh["geometry_mode"] = "modem_model_axes"
        mesh["configured_axes"] = None
        with self.assertRaisesRegex(ValueError, "mesh.modem_model_path is required"):
            config_from_dict(payload)

    def test_modem_axes_keeps_independent_air_layer_settings(self) -> None:
        payload = valid_mesh_config_payload()
        mesh = payload["mesh"]
        assert isinstance(mesh, dict)
        mesh["geometry_mode"] = "modem_model_axes"
        mesh["configured_axes"] = None
        mesh["modem_model_path"] = "input/model.rho"
        config = config_from_dict(payload, Path("C:/portable/configs"))
        self.assertEqual(config.mesh.air_layers.count, 4)
        self.assertEqual(config.mesh.air_layers.scheme, "geometric")
        self.assertEqual(config.mesh.air_layers.top_km, 100.0)
        self.assertEqual(config.mesh.air_layers.bottom_km, 0.25)

    def test_modem_fixed_height_air_requires_modem_axes_and_null_bottom(self) -> None:
        payload = valid_mesh_config_payload()
        mesh = payload["mesh"]
        assert isinstance(mesh, dict)
        air = mesh["air_layers"]
        assert isinstance(air, dict)
        air.update({"scheme": "modem_fixed_height", "bottom_km": None})
        with self.assertRaisesRegex(
            ValueError, "modem_fixed_height requires mesh.geometry_mode=modem_model_axes"
        ):
            config_from_dict(payload)

        mesh["geometry_mode"] = "modem_model_axes"
        mesh["configured_axes"] = None
        mesh["modem_model_path"] = "input/model.rho"
        config = config_from_dict(payload, Path("C:/portable/configs"))
        self.assertEqual(config.mesh.air_layers.scheme, "modem_fixed_height")
        self.assertIsNone(config.mesh.air_layers.bottom_km)

    def test_file_topography_requires_path(self) -> None:
        payload = valid_mesh_config_payload()
        topography = payload["topography"]
        assert isinstance(topography, dict)
        topography["mode"] = "file"
        with self.assertRaisesRegex(ValueError, "topography.path is required"):
            config_from_dict(payload)

    def test_mesh_config_round_trip_includes_kind(self) -> None:
        config = config_from_dict(valid_mesh_config_payload())
        self.assertEqual(config_to_dict(config)["config_kind"], "mesh")


if __name__ == "__main__":
    unittest.main()
