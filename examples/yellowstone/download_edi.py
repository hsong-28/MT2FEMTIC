"""Download and verify the 98 public EarthScope EDI files for this example."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Copied Yellowstone example directory",
    )
    root = parser.parse_args().root.resolve()
    manifest = json.loads((root / "source-files.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    inventory = [
        line.strip()
        for line in (root / "edi-files.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if int(inventory[0]) != len(files) or inventory[1:] != [row["filename"] for row in files]:
        raise ValueError("EDI inventory and source manifest disagree")

    output = root / "0-EDI"
    output.mkdir(exist_ok=True)
    for row in files:
        name = row["filename"]
        if Path(name).name != name:
            raise ValueError(f"Invalid EDI filename: {name}")
        target = output / name
        if target.exists():
            if sha256(target) != row["sha256"]:
                raise ValueError(f"Existing EDI hash mismatch: {name}")
            continue
        temporary = output / f".{name}.part"
        request = urllib.request.Request(row["url"], headers={"User-Agent": "MT2FEMTIC/0.1"})
        try:
            with urllib.request.urlopen(request) as response, temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream)
            if temporary.stat().st_size != row["bytes"] or sha256(temporary) != row["sha256"]:
                raise ValueError(f"Downloaded EDI verification failed: {name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    actual = {path.name for path in output.glob("*.edi")}
    expected = {row["filename"] for row in files}
    if actual != expected:
        raise ValueError("0-EDI contains files outside the declared inventory")
    print(json.dumps({"edi_file_count": len(files), "status": "passed"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
