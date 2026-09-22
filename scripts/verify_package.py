#!/usr/bin/env python3
"""Verify that an MT2FEMTIC checkout satisfies public-release packaging gates."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import tomllib
from pathlib import Path


REQUIRED_FILES = (
    "LICENSE",
    "README.md",
    "CITATION.cff",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "src/mt2femtic/__init__.py",
    "tests/fixtures/femticpy/LICENSE",
)
TEXT_SUFFIXES = {
    ".cff",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
IGNORED_PARTS = {".git", ".venv", "__pycache__", "build", "dist"}
SDIST_METADATA_FILES = {"PKG-INFO", "setup.cfg"}
CHECKOUT_ONLY_PATHS = (
    ("work",),
    ("reports",),
    ("reference",),
    ("docs", "designs"),
    ("docs", "plans"),
)
PUBLIC_HIDDEN_NAMES = {".github", ".gitignore", ".gitattributes"}
DEVELOPER_PATH_RE = re.compile(
    r"(?:\b[A-Za-z]:[\\/](?:Users|2026-MSH|FEMTIC-DABIC|BrokenHill-ABIC-Case|private)\b|/(?:home|Users)/)",
    re.IGNORECASE,
)
RELEASE_PLACEHOLDERS = ("YOUR_" + "REPOSITORY_URL",)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_files(root: Path) -> list[Path]:
    source_checkout = (root / ".git").is_dir()

    def ignored(path: Path) -> bool:
        parts = path.relative_to(root).parts
        generated = any(
            part in IGNORED_PARTS or part.endswith(".egg-info") for part in parts
        ) or (len(parts) == 1 and parts[0] in SDIST_METADATA_FILES)
        checkout_only = source_checkout and any(
            parts[: len(prefix)] == prefix for prefix in CHECKOUT_ONLY_PATHS
        )
        return generated or checkout_only

    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.name != "SHA256SUMS.txt"
            and path.suffix != ".pyc"
            and not ignored(path)
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def write_checksum_manifest(root: Path) -> int:
    """Write SHA256SUMS.txt for every distributable file below *root*."""

    package = root.resolve()
    rows = [
        f"{sha256_file(path)}  {path.relative_to(package).as_posix()}"
        for path in release_files(package)
    ]
    (package / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="ascii")
    return len(rows)


def _package_version(path: Path) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def _citation_version(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "version":
            return value.strip().strip('"\'')
    return None


def _verify_checksums(root: Path, errors: list[str]) -> int:
    manifest = root / "SHA256SUMS.txt"
    if not manifest.is_file():
        return 0
    listed: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="ascii").splitlines(), 1):
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or relative in listed:
            errors.append(f"invalid checksum row: {line_number}")
            continue
        item = root / relative
        if not item.is_file():
            errors.append(f"checksummed file is missing: {relative}")
        elif sha256_file(item) != digest.lower():
            errors.append(f"checksum mismatch: {relative}")
        listed[relative] = digest.lower()
    actual = {path.relative_to(root).as_posix() for path in release_files(root)}
    if set(listed) != actual:
        errors.append("checksum coverage mismatch")
    return len(listed)


def verify_package(root: Path) -> dict[str, object]:
    package = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    for relative in REQUIRED_FILES:
        path = package / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing required file: {relative}")

    versions: dict[str, str | None] = {"pyproject": None, "package": None, "citation": None}
    pyproject = package / "pyproject.toml"
    package_init = package / "src" / "mt2femtic" / "__init__.py"
    citation = package / "CITATION.cff"
    if pyproject.is_file():
        versions["pyproject"] = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("version")
    if package_init.is_file():
        versions["package"] = _package_version(package_init)
    if citation.is_file():
        versions["citation"] = _citation_version(citation)
        citation_text = citation.read_text(encoding="utf-8")
        if "MT2FEMTIC contributors" in citation_text:
            errors.append("citation author metadata is still a placeholder")
    present_versions = {value for value in versions.values() if value is not None}
    if len(present_versions) > 1 or any(value is None for value in versions.values()):
        errors.append(f"version mismatch: {versions}")

    scanned_text_files = 0
    for path in release_files(package):
        relative = path.relative_to(package)
        if any(
            part.startswith(".") and (index != 0 or part not in PUBLIC_HIDDEN_NAMES)
            for index, part in enumerate(relative.parts)
        ):
            errors.append(f"local tooling artifact: {relative.as_posix()}")
        if "tests" in relative.parts or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned_text_files += 1
        if DEVELOPER_PATH_RE.search(text):
            errors.append(f"developer-specific path: {relative.as_posix()}")
        for placeholder in RELEASE_PLACEHOLDERS:
            if placeholder in text:
                errors.append(f"unresolved placeholder: {placeholder}")

    cache_files = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and (path.suffix == ".pyc" or "__pycache__" in path.parts)
    ]
    if cache_files:
        warnings.append(f"cache files present but excluded from release content: {len(cache_files)}")

    checksum_count = _verify_checksums(package, errors)
    if not (package / "SHA256SUMS.txt").is_file():
        warnings.append("SHA256SUMS.txt is not present; generate it for the release archive")

    return {
        "schema_version": 1,
        "root": str(package),
        "passed": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "versions": versions,
        "release_file_count": len(release_files(package)),
        "scanned_text_file_count": scanned_text_files,
        "checksum_entry_count": checksum_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--write-checksums",
        action="store_true",
        help="regenerate SHA256SUMS.txt before verification",
    )
    args = parser.parse_args()
    if args.write_checksums:
        write_checksum_manifest(args.root)
    report = verify_package(args.root)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
