from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from mt2femtic.data_pipeline import data
from mt2femtic.manifest import sha256_file
from mt2femtic.mesh_pipeline import mesh
from tests.test_data_config import valid_data_config_payload
from tests.test_mesh_config import valid_mesh_config_payload


FIXTURES = Path(__file__).parent / "fixtures"
GENERATOR_SHA256 = "09d55ea38ba7136e1eda134ebf1d9de86d833982176bd14bdb02e1a75edad038"


def _generator() -> Path | None:
    value = os.environ.get("MT2FEMTIC_DHEXA")
    return Path(value) if value else None


def _write_configs(source: str, root: Path, generator: Path) -> tuple[Path, Path]:
    data_payload = valid_data_config_payload()
    data_payload["dataset_id"] = f"{source}_compact_smoke"
    data_payload["coordinates"].update(
        {
            "projected_crs": "EPSG:32632",
            "origin_easting_m": 500000.0,
            "origin_northing_m": 5538630.702867474,
        }
    )
    if source == "edi":
        edi_dir = root / "edi"
        edi_dir.mkdir()
        (edi_dir / "plus_iwt.edi").write_bytes(
            (FIXTURES / "edi" / "plus_iwt.edi").read_bytes()
        )
        data_payload["source"]["path"] = str(edi_dir)
        topography = root / "topography.dat"
        topography.write_text(
            "-5000 -5000 100\n-5000 0 150\n-5000 5000 200\n"
            "0 -5000 125\n0 0 175\n0 5000 225\n"
            "5000 -5000 150\n5000 0 200\n5000 5000 250\n",
            encoding="ascii",
        )
        data_payload["topography"].update(
            {"enabled": True, "path": str(topography), "columns": "north_east_elevation_m"}
        )
    else:
        data_payload["source"].update(
            {"type": "modem", "path": str(FIXTURES / "modem" / "survey.dat")}
        )
    data_payload["selection"]["periods_s"] = [1.0]

    mesh_payload = valid_mesh_config_payload()
    mesh_payload["mesh_id"] = f"{source}_compact_dhexa"
    mesh_payload["mesh"]["configured_axes"].update(
        {
            "x_max_km": 5.0,
            "x_uniform_limit_km": 5.0,
            "x_spacing_km": 1.0,
            "y_max_km": 5.0,
            "y_uniform_limit_km": 5.0,
            "y_spacing_km": 1.0,
            "z_max_km": 2.0,
            "z_spacing_km": 0.5,
        }
    )
    mesh_payload["mesh"]["air_layers"].update(
        {"count": 2, "top_km": 5.0, "bottom_km": 0.5}
    )
    mesh_payload["topography"]["mode"] = "native" if source == "edi" else "flat"
    mesh_payload["generator"].update(
        {
            "path": str(generator),
            "version": "v1.6.1",
            "sha256": GENERATOR_SHA256,
            "timeout_s": 120,
        }
    )
    data_config = root / f"{source}-data.json"
    mesh_config = root / f"{source}-mesh.json"
    data_config.write_text(json.dumps(data_payload, indent=2), encoding="utf-8")
    mesh_config.write_text(json.dumps(mesh_payload, indent=2), encoding="utf-8")
    return data_config, mesh_config


class ReleaseSmokeTests(unittest.TestCase):
    @unittest.skipUnless(_generator() and _generator().is_file(), "Set MT2FEMTIC_DHEXA")
    def test_edi_and_modem_generate_dhexa_outputs(self) -> None:
        generator = _generator()
        assert generator is not None
        required = {"dhexa/mesh.dat", "dhexa/resistivity_block_iter0.dat", "dhexa/MeshData.vtk"}
        for source in ("edi", "modem"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                data_config, mesh_config = _write_configs(source, root, generator)
                data_output = root / "data-output"
                mesh_output = root / "mesh-output"
                data_manifest = data(data_config, data_output)
                mesh_manifest = mesh(mesh_config, data_output, mesh_output)
                self.assertEqual(mesh_manifest.stages["products"].status, "passed")
                self.assertTrue(all((mesh_output / path).is_file() for path in required))
                for relative, expected in data_manifest.output_hashes.items():
                    self.assertEqual(sha256_file(data_output / relative), expected)
                for relative, expected in mesh_manifest.output_hashes.items():
                    self.assertEqual(sha256_file(mesh_output / relative), expected)


if __name__ == "__main__":
    unittest.main()
