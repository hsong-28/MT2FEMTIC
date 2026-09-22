#!/usr/bin/env python3
"""Public command-line interface for MT2FEMTIC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mt2femtic.data_pipeline import MANIFEST_NAME as DATA_MANIFEST_NAME
from mt2femtic.data_pipeline import data
from mt2femtic.mesh_pipeline import MANIFEST_NAME as MESH_MANIFEST_NAME
from mt2femtic.mesh_pipeline import mesh
from mt2femtic.validation import MANIFEST_NAME as VALIDATION_MANIFEST_NAME
from mt2femtic.validation import validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mt2femtic",
        description="Prepare validated 3-D FEMTIC data and DHEXA meshes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    conversion = subparsers.add_parser("convert", help="Convert FEMTIC, ModEM, or Jif3D observations without reprocessing.")
    conversion.add_argument("--from", dest="source_format", choices=("femtic", "modem", "jif3d"), required=True)
    conversion.add_argument("--to", dest="target_format", choices=("femtic", "modem", "jif3d"), required=True)
    conversion.add_argument("--input", type=Path, required=True)
    conversion.add_argument("--output", type=Path, required=True, help="New output directory.")
    conversion.add_argument("--surface-depth-m", type=float, help="Declare a common positive-down depth for FEMTIC surface observations.")
    validation = subparsers.add_parser(
        "validate",
        help="Preflight one data or mesh configuration without scientific outputs.",
        description=(
            "Validate a data configuration, or validate a mesh configuration "
            "against an existing FEMTIC data directory. Writes evidence only."
        ),
        epilog=(
            "Example: mt2femtic validate --config CONFIG --output OUTPUT "
            "[--data DATA_DIRECTORY]"
        ),
    )
    validation.add_argument("--config", type=Path, required=True, metavar="CONFIG")
    validation.add_argument("--output", type=Path, required=True, metavar="OUTPUT")
    validation.add_argument("--data", type=Path, metavar="DATA_DIRECTORY")

    data_command = subparsers.add_parser(
        "data",
        help="Convert EDI or ModEM data into a validated FEMTIC data package.",
        description=(
            "Run only the data stage. Writes FEMTIC inputs and QA figures; "
            "does not generate a mesh."
        ),
        epilog=(
            "Example: mt2femtic data --config DATA_CONFIG --output DATA_OUTPUT"
        ),
    )
    data_command.add_argument(
        "--config", type=Path, required=True, metavar="DATA_CONFIG"
    )
    data_command.add_argument(
        "--output", type=Path, required=True, metavar="DATA_OUTPUT"
    )

    mesh_command = subparsers.add_parser(
        "mesh",
        help="Generate one DHEXA mesh from an existing FEMTIC data directory.",
        description=(
            "Run only the hexahedral mesh stage. Reads the data directory "
            "without modifying it and writes a separate mesh output."
        ),
        epilog=(
            "Example: mt2femtic mesh --config MESH_CONFIG --data DATA_DIRECTORY "
            "--output MESH_OUTPUT"
        ),
    )
    mesh_command.add_argument(
        "--config", type=Path, required=True, metavar="MESH_CONFIG"
    )
    mesh_command.add_argument(
        "--data", type=Path, required=True, metavar="DATA_DIRECTORY"
    )
    mesh_command.add_argument(
        "--output", type=Path, required=True, metavar="MESH_OUTPUT"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "convert":
            from .conversion import convert
            result = convert(args.input, args.output, args.source_format, args.target_format, args.surface_depth_m)
            print(f"command=convert status=passed observations={result['complex_observation_count']} output={args.output.resolve()}")
            return 0
        elif args.command == "validate":
            manifest = validate(args.config, args.output, args.data)
            manifest_name = VALIDATION_MANIFEST_NAME
        elif args.command == "data":
            manifest = data(args.config, args.output)
            manifest_name = DATA_MANIFEST_NAME
        else:
            manifest = mesh(args.config, args.data, args.output)
            manifest_name = MESH_MANIFEST_NAME
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"command={args.command} status=failed error={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    failed = next(
        (name for name, stage in manifest.stages.items() if stage.status == "failed"),
        None,
    )
    status = "passed" if failed is None else "failed"
    message = (
        f"command={args.command} id={manifest.mesh_id or manifest.dataset_id} "
        f"status={status} manifest={Path(args.output).resolve() / manifest_name}"
    )
    if failed is not None:
        message += f" failed_gate={failed}"
    print(message)
    return 0 if failed is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
