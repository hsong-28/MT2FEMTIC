"""Write DHEXA inputs without running the external mesh generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mt2femtic.single_survey import write_meshgen_stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Survey root")
    report = write_meshgen_stage(parser.parse_args().root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
