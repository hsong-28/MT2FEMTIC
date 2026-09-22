"""DHEXA mesh input, execution, and product validation."""

from __future__ import annotations

import math
import os
import re
import subprocess
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import GeneratorConfig, MeshConfig, MeshTopographyConfig
from .manifest import sha256_file
from .model import ModemModel, Station


@dataclass(frozen=True)
class MeshAxes:
    x_km: tuple[float, ...]
    y_km: tuple[float, ...]
    z_km: tuple[float, ...]


@dataclass(frozen=True)
class GeneratorIdentity:
    path: Path
    version: str
    sha256: str
    timeout_s: int


@dataclass(frozen=True)
class ProductSummary:
    node_count: int
    element_count: int
    parameter_count: int
    active_parameter_count: int
    minimum_resistivity_ohm_m: float
    maximum_resistivity_ohm_m: float


@dataclass(frozen=True)
class _BlockRow:
    block_id: int
    resistivity: float
    minimum: float
    maximum: float
    weight: float
    fixed_flag: int


@dataclass(frozen=True)
class _BlockFile:
    element_count: int
    parameter_count: int
    element_parameters: tuple[tuple[int, int], ...]
    blocks: tuple[_BlockRow, ...]


def _air_coordinates(layer_count: int, bottom_km: float, top_km: float) -> tuple[float, ...]:
    if layer_count < 1:
        raise ValueError("Air layer count must be at least one")
    if not 0.0 < bottom_km <= top_km:
        raise ValueError("Air bounds must satisfy 0 < bottom <= top")
    if layer_count == 1:
        positive = [top_km]
    else:
        low = math.log10(bottom_km)
        step = (math.log10(top_km) - low) / (layer_count - 1)
        positive = [10.0 ** (low + index * step) for index in range(layer_count)]
    return tuple(-value for value in reversed(positive))


def _modem_fixed_height_air_coordinates(
    first_earth_layer_m: float,
    layer_count: int,
    maximum_height_km: float,
) -> tuple[float, ...]:
    if first_earth_layer_m <= 0.0 or layer_count < 1 or maximum_height_km <= 0.0:
        raise ValueError("Invalid ModEM fixed-height air-layer specification")
    maximum_height_m = maximum_height_km * 1000.0
    if maximum_height_m <= first_earth_layer_m:
        raise ValueError("ModEM air maximum height must exceed the first earth layer")
    logarithmic_height = math.log10(first_earth_layer_m)
    logarithmic_step = (
        math.log10(maximum_height_m) - logarithmic_height
    ) / layer_count
    cumulative_height_m = 0.0
    boundaries_m: list[float] = []
    for _ in range(layer_count):
        thickness_m = (
            10.0 ** (logarithmic_height + logarithmic_step)
            - 10.0**logarithmic_height
        )
        cumulative_height_m += thickness_m
        boundaries_m.append(cumulative_height_m)
        logarithmic_height += logarithmic_step
    return tuple(-value / 1000.0 for value in reversed(boundaries_m))


def _positive_padded_coordinates(
    maximum_km: float,
    spacing_km: float,
    padding_factor: float,
    padding_start_km: float,
) -> tuple[float, ...]:
    if maximum_km <= 0.0 or spacing_km <= 0.0 or padding_factor <= 1.0:
        raise ValueError("Mesh extent, spacing, and padding factor are invalid")
    coordinates = [0.0]
    spacing = spacing_km
    while coordinates[-1] < maximum_km:
        coordinates.append(coordinates[-1] + spacing)
        if coordinates[-1] >= padding_start_km:
            spacing *= padding_factor
        if len(coordinates) > 100000:
            raise ValueError("Mesh axis generation exceeded 100000 coordinates")
    return tuple(coordinates)


def _symmetric_coordinates(
    maximum_km: float,
    uniform_limit_km: float,
    spacing_km: float,
    padding_factor: float,
) -> tuple[float, ...]:
    positive = _positive_padded_coordinates(
        maximum_km,
        spacing_km,
        padding_factor,
        uniform_limit_km,
    )
    return tuple(-value for value in reversed(positive)) + positive[1:]


def _validate_axes(axes: MeshAxes) -> MeshAxes:
    for name, values in (("X", axes.x_km), ("Y", axes.y_km), ("Z", axes.z_km)):
        if len(values) < 2:
            raise ValueError(f"{name} axis must contain at least two coordinates")
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{name} axis contains a non-finite coordinate")
        if not all(left < right for left, right in zip(values, values[1:])):
            raise ValueError(f"{name} axis must be strictly increasing")
    return axes


def build_configured_axes(config: MeshConfig) -> MeshAxes:
    if config.backend != "dhexa" or config.geometry_mode != "configured_axes":
        raise ValueError("Configured-axis builder requires the dhexa configured_axes mode")
    values = config.configured_axes
    if values is None:
        raise ValueError("Configured-axis builder requires configured axes")
    x_axis = _symmetric_coordinates(
        values.x_max_km,
        values.x_uniform_limit_km,
        values.x_spacing_km,
        values.x_padding_factor,
    )
    y_axis = _symmetric_coordinates(
        values.y_max_km,
        values.y_uniform_limit_km,
        values.y_spacing_km,
        values.y_padding_factor,
    )
    if config.air_layers.scheme != "geometric":
        raise ValueError("Configured axes require geometric air layers")
    assert config.air_layers.bottom_km is not None
    air = _air_coordinates(
        config.air_layers.count,
        config.air_layers.bottom_km,
        config.air_layers.top_km,
    )
    earth = _positive_padded_coordinates(
        values.z_max_km,
        values.z_spacing_km,
        values.z_padding_factor,
        0.0,
    )
    return _validate_axes(MeshAxes(x_axis, y_axis, air + earth))


def _cumulative_edges_km(origin_m: float, widths_m: Sequence[float]) -> tuple[float, ...]:
    edges = [origin_m / 1000.0]
    for width_m in widths_m:
        if not math.isfinite(width_m) or width_m <= 0.0:
            raise ValueError("ModEM model widths must be finite and positive")
        edges.append(edges[-1] + width_m / 1000.0)
    return tuple(round(value, 12) for value in edges)


def build_modem_axes(model: ModemModel, config: MeshConfig) -> MeshAxes:
    if config.backend != "dhexa" or config.geometry_mode != "modem_model_axes":
        raise ValueError("ModEM-axis builder requires the dhexa modem_model_axes mode")
    if abs(model.rotation_deg) > 1.0e-12:
        raise ValueError(
            "A nonzero ModEM model rotation is not supported without a separately "
            "validated observation rotation"
        )
    if abs(model.origin_m[2]) > 1.0e-12:
        raise ValueError("ModEM model depth origin must be zero for DHEXA preparation")
    if config.air_layers.scheme == "geometric":
        assert config.air_layers.bottom_km is not None
        air = _air_coordinates(
            config.air_layers.count,
            config.air_layers.bottom_km,
            config.air_layers.top_km,
        )
    else:
        air = _modem_fixed_height_air_coordinates(
            model.depth_widths_m[0],
            config.air_layers.count,
            config.air_layers.top_km,
        )
    earth = _cumulative_edges_km(model.origin_m[2], model.depth_widths_m)
    axes = MeshAxes(
        _cumulative_edges_km(model.origin_m[0], model.north_widths_m),
        _cumulative_edges_km(model.origin_m[1], model.east_widths_m),
        air + earth,
    )
    return _validate_axes(axes)


def validate_station_mesh_bounds(stations: Sequence[Station], axes: MeshAxes) -> None:
    minimum_x, maximum_x = axes.x_km[0], axes.x_km[-1]
    minimum_y, maximum_y = axes.y_km[0], axes.y_km[-1]
    for station in stations:
        if station.model_x_km is None or station.model_y_km is None:
            raise ValueError(f"Station {station.name} has no normalized model coordinates")
        if not (
            minimum_x <= station.model_x_km <= maximum_x
            and minimum_y <= station.model_y_km <= maximum_y
        ):
            raise ValueError(f"Station {station.name} lies outside the DHEXA mesh")


def write_meshgen_input(
    axes: MeshAxes,
    mesh: MeshConfig,
    topography: MeshTopographyConfig,
    output_path: Path,
    topography_path: Path | None,
) -> None:
    _validate_axes(axes)
    if mesh.backend != "dhexa":
        raise ValueError("mesh.backend must be dhexa")
    if topography.enabled and topography_path is None:
        raise ValueError("Enabled topography requires a staged topography file")
    if not topography.enabled and topography_path is not None:
        raise ValueError("Flat-earth configuration cannot reference a topography file")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "DIVISION_NUMBERS",
        f"{len(axes.x_km) - 1:5d} {len(axes.y_km) - 1:5d} {len(axes.z_km) - 1:5d}",
        "X_COORDINATES",
    ]
    lines.extend(f"{value: .12e}" for value in axes.x_km)
    lines.append("Y_COORDINATES")
    lines.extend(f"{value: .12e}" for value in axes.y_km)
    lines.append("Z_COORDINATES")
    lines.extend(f"{value: .12e}" for value in axes.z_km)
    lines.extend(
        (
            "INITIAL_RESISTIVITY",
            f"{mesh.initial_resistivity_ohm_m: .12e}",
            "AIR_RESISTIVITY",
            f"{mesh.air_resistivity_ohm_m: .12e}",
            "SEA_RESISTIVITY",
            f"{mesh.sea_resistivity_ohm_m: .12e}",
            "ANOMALIES",
            "0",
        )
    )
    if topography.enabled:
        assert topography_path is not None
        lines.extend(
            (
                "TOPO",
                topography_path.name,
                str(topography.max_interpolation_points),
                f"{topography.search_radius_km:.12g}",
                f"{topography.tolerance_km:.12g}",
            )
        )
    lines.append("END")
    destination.write_text("\n".join(lines) + "\n", encoding="ascii")


def verify_generator(config: GeneratorConfig) -> GeneratorIdentity:
    path = Path(config.path)
    if not path.is_file():
        raise FileNotFoundError(f"DHEXA generator does not exist: {path}")
    actual_hash = sha256_file(path)
    if actual_hash.lower() != config.sha256.lower():
        raise ValueError(
            f"DHEXA generator SHA-256 mismatch: expected {config.sha256}, "
            f"found {actual_hash}"
        )
    return GeneratorIdentity(path.resolve(), config.version, actual_hash, config.timeout_s)


def _windows_to_wsl_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    match = re.fullmatch(r"([A-Za-z]):/(.*)", text)
    if match is None:
        raise ValueError(f"Cannot convert Windows path to WSL path: {path}")
    return f"/mnt/{match.group(1).lower()}/{match.group(2)}"


def _is_elf(path: Path) -> bool:
    try:
        return Path(path).read_bytes()[:4] == b"\x7fELF"
    except OSError:
        return False


def build_generator_command(
    executable: Path,
    work_dir: Path,
    *,
    platform: str | None = None,
    executable_is_elf: bool | None = None,
) -> list[str]:
    current_platform = platform or ("windows" if os.name == "nt" else "linux")
    is_elf = _is_elf(executable) if executable_is_elf is None else executable_is_elf
    if current_platform == "windows" and is_elf:
        return [
            "wsl.exe",
            "--cd",
            _windows_to_wsl_path(Path(work_dir).resolve()),
            _windows_to_wsl_path(Path(executable).resolve()),
        ]
    return [str(Path(executable).resolve())]


def run_generator(
    identity: GeneratorIdentity,
    work_dir: Path,
) -> subprocess.CompletedProcess[str]:
    directory = Path(work_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    command = build_generator_command(identity.path, directory)
    try:
        result = subprocess.run(
            command,
            cwd=directory,
            shell=False,
            capture_output=True,
            text=True,
            timeout=identity.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"DHEXA generator exceeded timeout of {identity.timeout_s} s"
        ) from exc
    (directory / "meshgen.stdout").write_text(result.stdout, encoding="utf-8")
    (directory / "meshgen.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"DHEXA generator exited with exit code {result.returncode}")
    if result.stderr.strip():
        raise RuntimeError("DHEXA generator wrote non-whitespace standard error")
    version_marker = f"Start Mesh Generation. Version {identity.version}"
    if version_marker not in result.stdout:
        raise RuntimeError(f"DHEXA generator did not report {identity.version}")
    if "End Mesh Generation." not in result.stdout:
        raise RuntimeError("DHEXA generator did not reach its completion marker")
    return result


def _read_block_file(path: Path) -> _BlockFile:
    lines = [
        line.strip()
        for line in Path(path).read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError(f"Empty resistivity block file: {path}")
    header = lines[0].split()
    if len(header) != 2:
        raise ValueError("Resistivity block header must contain two integers")
    try:
        element_count, parameter_count = (int(value) for value in header)
    except ValueError as exc:
        raise ValueError("Invalid resistivity block header") from exc
    if element_count <= 0 or parameter_count <= 0:
        raise ValueError("Resistivity block counts must be positive")
    if len(lines) != 1 + element_count + parameter_count:
        raise ValueError("Resistivity block row count is inconsistent with its header")
    element_parameters: list[tuple[int, int]] = []
    for expected_element, line in enumerate(lines[1 : 1 + element_count]):
        tokens = line.split()
        if len(tokens) != 2:
            raise ValueError("Element-to-parameter row must contain two integers")
        element_id, parameter_id = (int(value) for value in tokens)
        if element_id != expected_element:
            raise ValueError("Element IDs must be sequential from zero")
        if not 0 <= parameter_id < parameter_count:
            raise ValueError(f"Element {element_id} references invalid parameter {parameter_id}")
        element_parameters.append((element_id, parameter_id))
    blocks: list[_BlockRow] = []
    for expected_block, line in enumerate(lines[1 + element_count :]):
        tokens = line.split()
        if len(tokens) != 6:
            raise ValueError("Parameter block row must contain six columns")
        block_id = int(tokens[0])
        numeric = [float(value) for value in tokens[1:5]]
        fixed_flag = int(tokens[5])
        if block_id != expected_block:
            raise ValueError("Parameter block IDs must be sequential from zero")
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError(f"Parameter block {block_id} contains non-finite values")
        resistivity, minimum, maximum, weight = numeric
        if min(resistivity, minimum, maximum, weight) <= 0.0 or minimum > maximum:
            raise ValueError(f"Parameter block {block_id} contains invalid bounds or values")
        if fixed_flag not in {0, 1}:
            raise ValueError(f"Parameter block {block_id} has invalid fixed flag")
        blocks.append(
            _BlockRow(block_id, resistivity, minimum, maximum, weight, fixed_flag)
        )
    return _BlockFile(
        element_count,
        parameter_count,
        tuple(element_parameters),
        tuple(blocks),
    )


def _mesh_header_counts(path: Path) -> tuple[int, int]:
    with Path(path).open("r", encoding="ascii") as stream:
        mesh_type = stream.readline().strip()
        if mesh_type != "DHEXA":
            raise ValueError(f"Expected DHEXA mesh, found {mesh_type or 'empty header'}")
        try:
            node_count = int(stream.readline())
        except ValueError as exc:
            raise ValueError("Invalid DHEXA node count") from exc
        if node_count <= 0:
            raise ValueError("DHEXA node count must be positive")
        for expected_node in range(node_count):
            tokens = stream.readline().split()
            if len(tokens) != 4:
                raise ValueError(f"Invalid DHEXA node row {expected_node}")
            node_id = int(tokens[0])
            coordinates = [float(value) for value in tokens[1:]]
            if node_id != expected_node or not all(math.isfinite(value) for value in coordinates):
                raise ValueError(f"Invalid DHEXA node row {expected_node}")
        try:
            element_count = int(stream.readline())
        except ValueError as exc:
            raise ValueError("Invalid DHEXA element count") from exc
        if element_count <= 0:
            raise ValueError("DHEXA element count must be positive")
    return node_count, element_count


def validate_dhexa_products(
    work_dir: Path,
    stage_started_at_ns: int | None = None,
) -> ProductSummary:
    directory = Path(work_dir)
    required = (
        directory / "mesh.dat",
        directory / "resistivity_block_iter0.dat",
        directory / "MeshData.vtk",
    )
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty DHEXA product: {path.name}")
        if stage_started_at_ns is not None and path.stat().st_mtime_ns < stage_started_at_ns:
            raise ValueError(f"DHEXA product predates the current run: {path.name}")
    node_count, element_count = _mesh_header_counts(required[0])
    block_file = _read_block_file(required[1])
    if block_file.element_count != element_count:
        raise ValueError("mesh.dat and resistivity block element counts differ")
    active = [block for block in block_file.blocks if block.fixed_flag == 0]
    resistivities = [block.resistivity for block in block_file.blocks]
    return ProductSummary(
        node_count=node_count,
        element_count=element_count,
        parameter_count=block_file.parameter_count,
        active_parameter_count=len(active),
        minimum_resistivity_ohm_m=min(resistivities),
        maximum_resistivity_ohm_m=max(resistivities),
    )


def _iter_numeric_tokens(stream, count: int, label: str, converter):
    consumed = 0
    while consumed < count:
        line = stream.readline()
        if not line:
            raise ValueError(f"Unexpected end of VTK file while reading {label}")
        tokens = line.split()
        if consumed + len(tokens) > count:
            raise ValueError(f"VTK {label} contains more values than declared")
        for token in tokens:
            consumed += 1
            yield converter(token)


def _read_vtk_cell_centers_and_blocks(
    path: Path,
) -> tuple[array, array]:
    with Path(path).open("r", encoding="ascii") as stream:
        line = stream.readline()
        while line and not line.startswith("POINTS "):
            line = stream.readline()
        if not line:
            raise ValueError("VTK file has no POINTS section")
        point_header = line.split()
        point_count = int(point_header[1])
        points = array(
            "d",
            _iter_numeric_tokens(stream, point_count * 3, "points", float),
        )

        line = stream.readline()
        while line and not line.startswith("CELLS "):
            line = stream.readline()
        if not line:
            raise ValueError("VTK file has no CELLS section")
        cell_header = line.split()
        cell_count = int(cell_header[1])
        integer_count = int(cell_header[2])
        if integer_count != cell_count * 9:
            raise ValueError("VTK CELLS token count is inconsistent")
        cell_values = iter(
            _iter_numeric_tokens(stream, integer_count, "cells", int)
        )
        centers = array("d")
        for _cell_index in range(cell_count):
            nodes_in_cell = next(cell_values)
            if nodes_in_cell != 8:
                raise ValueError("DHEXA VTK cells must each contain eight nodes")
            node_ids = tuple(next(cell_values) for _ in range(nodes_in_cell))
            if any(node_id < 0 or node_id >= point_count for node_id in node_ids):
                raise ValueError("VTK cell references an invalid point")
            for axis in range(3):
                centers.append(
                    sum(points[node_id * 3 + axis] for node_id in node_ids)
                    / nodes_in_cell
                )
        del points

        line = stream.readline()
        while line and not line.startswith("CELL_DATA "):
            line = stream.readline()
        if not line or int(line.split()[1]) != cell_count:
            raise ValueError("VTK CELL_DATA count is missing or inconsistent")
        line = stream.readline()
        while line and line.strip() != "SCALARS BlockID int":
            line = stream.readline()
        if not line:
            raise ValueError("VTK file has no BlockID cell data")
        if stream.readline().strip() != "LOOKUP_TABLE default":
            raise ValueError("VTK BlockID lookup table is missing")
        block_ids = array(
            "q",
            _iter_numeric_tokens(stream, cell_count, "BlockID", int),
        )
    return centers, block_ids


def _source_model_edges(model: ModemModel) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    return (
        tuple(value * 1000.0 for value in _cumulative_edges_km(model.origin_m[0], model.north_widths_m)),
        tuple(value * 1000.0 for value in _cumulative_edges_km(model.origin_m[1], model.east_widths_m)),
        tuple(value * 1000.0 for value in _cumulative_edges_km(model.origin_m[2], model.depth_widths_m)),
    )


def _source_resistivity_at(
    model: ModemModel,
    x_m: float,
    y_m: float,
    z_m: float,
) -> tuple[int, float]:
    north_edges, east_edges, depth_edges = _source_model_edges(model)
    indices = (
        bisect_right(north_edges, x_m) - 1,
        bisect_right(east_edges, y_m) - 1,
        bisect_right(depth_edges, z_m) - 1,
    )
    north_index, east_index, depth_index = indices
    north_count, east_count, depth_count = model.dimensions
    if not (
        0 <= north_index < north_count
        and 0 <= east_index < east_count
        and 0 <= depth_index < depth_count
    ):
        raise ValueError(
            f"Active DHEXA cell center ({x_m:g}, {y_m:g}, {z_m:g}) m "
            "lies outside the ModEM model"
        )
    modem_north_index = north_count - 1 - north_index
    source_index = (
        depth_index * north_count * east_count
        + east_index * north_count
        + modem_north_index
    )
    return source_index, model.resistivity_ohm_m[source_index]


def _expected_parameter_resistivity(
    model: ModemModel,
    work_dir: Path,
    blocks: _BlockFile,
) -> tuple[dict[int, float], set[int]]:
    centers, vtk_block_ids = _read_vtk_cell_centers_and_blocks(
        Path(work_dir) / "MeshData.vtk"
    )
    if len(centers) != blocks.element_count * 3 or len(vtk_block_ids) != blocks.element_count:
        raise ValueError("VTK and resistivity block element counts differ")
    fixed = {block.block_id for block in blocks.blocks if block.fixed_flag == 1}
    expected: dict[int, float] = {}
    represented_source_cells: set[int] = set()
    for element_id, block_id in enumerate(vtk_block_ids):
        if block_id in fixed:
            continue
        center = tuple(centers[element_id * 3 + axis] for axis in range(3))
        source_index, resistivity = _source_resistivity_at(model, *center)
        represented_source_cells.add(source_index)
        previous = expected.get(block_id)
        if previous is not None and not math.isclose(previous, resistivity, rel_tol=1.0e-12, abs_tol=0.0):
            raise ValueError(
                f"DHEXA parameter {block_id} spans different ModEM resistivities"
            )
        expected[block_id] = resistivity
    active = {block.block_id for block in blocks.blocks if block.fixed_flag == 0}
    missing = sorted(active - set(expected))
    if missing:
        raise ValueError(f"Active DHEXA parameter has no VTK cell: {missing[0]}")
    return expected, represented_source_cells


def _write_block_file(path: Path, source: _BlockFile, resistivities: dict[int, float]) -> None:
    lines = [f"{source.element_count:10d}{source.parameter_count:10d}"]
    lines.extend(
        f"{element_id:10d}{parameter_id:10d}"
        for element_id, parameter_id in source.element_parameters
    )
    for block in source.blocks:
        resistivity = resistivities.get(block.block_id, block.resistivity)
        lines.append(
            f"{block.block_id:10d}{resistivity:24.12e}"
            f"{block.minimum:15.6e}{block.maximum:15.6e}"
            f"{block.weight:15.6e}{block.fixed_flag:10d}"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def write_source_model_block(
    model: ModemModel,
    work_dir: Path,
    output_path: Path,
) -> dict[str, object]:
    if abs(model.rotation_deg) > 1.0e-12:
        raise ValueError("Cannot map a nonzero-rotation ModEM model")
    blocks = _read_block_file(Path(work_dir) / "resistivity_block_iter0.dat")
    expected, represented = _expected_parameter_resistivity(model, work_dir, blocks)
    _write_block_file(Path(output_path), blocks, expected)
    values = list(expected.values())
    return {
        "mapped_parameter_count": len(expected),
        "represented_source_cell_count": len(represented),
        "source_cell_count": len(model.resistivity_ohm_m),
        "minimum_mapped_resistivity_ohm_m": min(values),
        "maximum_mapped_resistivity_ohm_m": max(values),
        "north_order_mapping": "reverse ModEM N-to-S rows into FEMTIC S-to-N order",
    }


def verify_model_round_trip(
    model: ModemModel,
    work_dir: Path,
    output_path: Path,
    tolerance: float,
) -> float:
    if tolerance <= 0.0 or not math.isfinite(tolerance):
        raise ValueError("Model round-trip tolerance must be finite and positive")
    source_blocks = _read_block_file(Path(work_dir) / "resistivity_block_iter0.dat")
    expected, _represented = _expected_parameter_resistivity(model, work_dir, source_blocks)
    output_blocks = _read_block_file(Path(output_path))
    if (
        output_blocks.element_parameters != source_blocks.element_parameters
        or output_blocks.parameter_count != source_blocks.parameter_count
    ):
        raise ValueError("Mapped model changed the DHEXA element-to-parameter contract")
    maximum = 0.0
    for block_id, expected_value in expected.items():
        actual_value = output_blocks.blocks[block_id].resistivity
        difference = abs(actual_value - expected_value) / max(abs(expected_value), 1.0e-30)
        maximum = max(maximum, difference)
    if maximum > tolerance:
        raise ValueError(
            f"ModEM-to-DHEXA model round trip {maximum:.6g} exceeds {tolerance:.6g}"
        )
    return maximum
