from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_data_config import valid_data_config_payload
from tests.test_femtic_io import write_femtic_trio
from tests.test_mesh_config import valid_mesh_config_payload


EDI_FIXTURE = Path(__file__).parent / "fixtures" / "edi" / "plus_iwt.edi"


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _data_config(self) -> Path:
        edi = self.root / "edi"
        edi.mkdir()
        (edi / "S01.edi").write_bytes(EDI_FIXTURE.read_bytes())
        payload = valid_data_config_payload()
        payload["source"]["path"] = str(edi)  # type: ignore[index]
        payload["coordinates"].update(  # type: ignore[union-attr]
            {
                "projected_crs": "EPSG:32632",
                "origin_easting_m": 500000.0,
                "origin_northing_m": 5538630.702867474,
            }
        )
        payload["selection"]["periods_s"] = [1.0]  # type: ignore[index]
        path = self.root / "data.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _mesh_config(self) -> Path:
        generator = self.root / "makeDHexaMesh"
        generator.write_bytes(b"verified-only")
        payload = valid_mesh_config_payload()
        payload["generator"].update(  # type: ignore[union-attr]
            {
                "path": str(generator),
                "sha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
            }
        )
        axes = payload["mesh"]["configured_axes"]  # type: ignore[index]
        axes.update(  # type: ignore[union-attr]
            {
                "x_max_km": 2.0,
                "x_uniform_limit_km": 1.0,
                "x_spacing_km": 1.0,
                "y_max_km": 2.0,
                "y_uniform_limit_km": 1.0,
                "y_spacing_km": 1.0,
                "z_max_km": 2.0,
                "z_spacing_km": 0.5,
            }
        )
        path = self.root / "mesh.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_data_validation_writes_only_evidence(self) -> None:
        from mt2femtic.validation import validate

        output = self.root / "validation"
        result = validate(self._data_config(), output)
        self.assertEqual(result.stages["femtic_input"].status, "passed")
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {"mt2femtic_validation.log", "mt2femtic_validation_manifest.json"},
        )

    @patch("mt2femtic.dhexa.run_generator")
    def test_mesh_validation_verifies_generator_without_running_it(
        self, run_mock
    ) -> None:
        from mt2femtic.validation import validate

        data_root = self.root / "external"
        write_femtic_trio(data_root)
        output = self.root / "validation"
        result = validate(self._mesh_config(), output, data_root)
        self.assertEqual(result.stages["dhexa_input"].status, "passed")
        self.assertEqual(result.executable["version"], "v1.6.1")
        run_mock.assert_not_called()
        self.assertFalse(any(path.name == "meshgen.inp" for path in output.rglob("*")))

    def test_validate_data_rejects_data_directory_argument(self) -> None:
        from mt2femtic.validation import validate

        with self.assertRaisesRegex(ValueError, "data configuration does not accept --data"):
            validate(self._data_config(), self.root / "validation", self.root)

    def test_validate_mesh_requires_data_directory_argument(self) -> None:
        from mt2femtic.validation import validate

        with self.assertRaisesRegex(ValueError, "mesh configuration requires --data"):
            validate(self._mesh_config(), self.root / "validation")

    def test_recorded_failure_has_gate_and_only_evidence(self) -> None:
        from mt2femtic.validation import validate

        config = self._data_config()
        payload = json.loads(config.read_text(encoding="utf-8"))
        payload["source"]["path"] = str(self.root / "missing")
        config.write_text(json.dumps(payload), encoding="utf-8")
        output = self.root / "validation"
        result = validate(config, output)
        self.assertEqual(result.stages["source"].status, "failed")
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {"mt2femtic_validation.log", "mt2femtic_validation_manifest.json"},
        )


if __name__ == "__main__":
    unittest.main()
