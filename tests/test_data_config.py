from __future__ import annotations

import unittest
from pathlib import Path

from mt2femtic.config import config_from_dict, config_to_dict


def valid_data_config_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "config_kind": "data",
        "dataset_id": "unit-data",
        "source": {
            "type": "edi",
            "path": "input/edi",
            "impedance_unit": "mv_per_km_per_nt",
            "time_convention": "exp_plus_iwt",
            "allow_time_convention_override": False,
            "edi_pattern": "*.edi",
        },
        "coordinates": {
            "geographic_crs": "EPSG:4326",
            "projected_crs": "EPSG:32633",
            "origin_easting_m": 500000.0,
            "origin_northing_m": 6000000.0,
            "model_axis_azimuth_deg": 0.0,
            "vertical_datum_elevation_m": 0.0,
            "round_trip_tolerance_m": 0.01,
            "modem_axis_convention": "north_east",
        },
        "selection": {
            "frequency_policy": "exact",
            "periods_s": [1.0, 10.0],
            "relative_tolerance": 0.001,
            "impedance_error_floor_fraction": 0.01,
            "vtf_error_floor_absolute": 0.01,
            "max_vtf_period_s": 10000.0,
        },
        "topography": {
            "enabled": False,
            "path": None,
            "columns": None,
        },
        "femtic": {
            "observation_refinement": {
                "radius_km": 50.0,
                "level": 24,
                "weight": 0.5,
            }
        },
        "run": {"overwrite": False, "resume": False},
    }


class DataConfigTests(unittest.TestCase):
    def test_data_config_has_no_mesh_or_generator_dependency(self) -> None:
        try:
            config = config_from_dict(
                valid_data_config_payload(), Path("C:/portable/configs")
            )
        except ValueError as exc:
            self.fail(str(exc))
        self.assertEqual(config.config_kind, "data")
        self.assertEqual(config.dataset_id, "unit-data")
        self.assertFalse(hasattr(config, "mesh"))
        self.assertFalse(hasattr(config, "generator"))
        self.assertEqual(config.femtic.observation_refinement.level, 24)

    def test_misleading_observation_owner_name_is_rejected(self) -> None:
        payload = valid_data_config_payload()
        femtic = payload["femtic"]
        assert isinstance(femtic, dict)
        femtic["observation_owner"] = femtic.pop("observation_refinement")
        with self.assertRaisesRegex(
            ValueError, "Unknown configuration key: femtic.observation_owner"
        ):
            config_from_dict(payload)

    def test_data_paths_resolve_from_configuration_directory(self) -> None:
        config = config_from_dict(
            valid_data_config_payload(), Path("C:/portable/configs")
        )
        self.assertEqual(
            config.source.path,
            Path("C:/portable/configs/input/edi").resolve(),
        )

    def test_edi_inventory_and_station_name_source_are_explicit(self) -> None:
        payload = valid_data_config_payload()
        source = payload["source"]
        assert isinstance(source, dict)
        source["edi_list_file"] = "input/edi/edi_list_3D.txt"
        source["station_name_source"] = "file_stem"
        config = config_from_dict(payload, Path("C:/portable/configs"))
        self.assertEqual(
            config.source.edi_list_file,
            Path("C:/portable/configs/input/edi/edi_list_3D.txt").resolve(),
        )
        self.assertEqual(config.source.station_name_source, "file_stem")

    def test_station_name_source_is_strict(self) -> None:
        payload = valid_data_config_payload()
        source = payload["source"]
        assert isinstance(source, dict)
        source["station_name_source"] = "guess"
        with self.assertRaisesRegex(ValueError, "station_name_source"):
            config_from_dict(payload)

    def test_data_config_rejects_mesh_key(self) -> None:
        payload = valid_data_config_payload()
        payload["mesh"] = {}
        with self.assertRaisesRegex(ValueError, "Unknown configuration key: mesh"):
            config_from_dict(payload)

    def test_frequency_policy_is_strict(self) -> None:
        payload = valid_data_config_payload()
        selection = payload["selection"]
        assert isinstance(selection, dict)
        selection["frequency_policy"] = "cluster"
        with self.assertRaisesRegex(
            ValueError, "selection.frequency_policy must be exact or log_linear"
        ):
            config_from_dict(payload)

    def test_data_config_round_trip_includes_kind(self) -> None:
        config = config_from_dict(valid_data_config_payload())
        self.assertEqual(config_to_dict(config)["config_kind"], "data")


if __name__ == "__main__":
    unittest.main()
