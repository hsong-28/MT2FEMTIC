"""Co-located Jif3D NetCDF observations; upstream contract: docs/conversion.md."""

from pathlib import Path

import numpy as np

from .model import ResponseSample, Station, Survey


COMPONENTS = {"ZXX": "Zxx", "ZXY": "Zxy", "ZYX": "Zyx", "ZYY": "Zyy", "TX": "Tx", "TY": "Ty"}


def _dataset():
    try:
        from netCDF4 import Dataset
    except ImportError as exc:
        raise ValueError('Jif3D conversion requires: python -m pip install "mt2femtic[conversion]"') from exc
    return Dataset


def _array(nc, name, dimensions, *, units=None):
    if name not in nc.variables:
        raise ValueError(f"Jif3D variable is required: {name}")
    variable = nc[name]
    if variable.dimensions != dimensions:
        raise ValueError(f"Jif3D {name} dimensions must be {dimensions}")
    if units is not None and getattr(variable, "units", units).strip().lower() != units.lower():
        raise ValueError(f"Jif3D {name} units must be {units}")
    values = variable[:]
    if np.any(np.ma.getmaskarray(values)) or not np.all(np.isfinite(values)):
        raise ValueError(f"Jif3D {name} contains missing or non-finite values")
    return np.asarray(values)


def read_jif3d(path: Path) -> Survey:
    """Read the ordinary MT/Tipper subset of the upstream NetCDF schema."""
    with _dataset()(path) as nc:
        n = len(nc.dimensions["StationNumber"]) if "StationNumber" in nc.dimensions else 0
        if n == 0:
            raise ValueError("Jif3D StationNumber must be nonempty")
        frequencies = _array(nc, "Frequency", ("Frequency",), units="Hz")
        if not len(frequencies) or np.any(frequencies <= 0) or len(set(frequencies)) != len(frequencies):
            raise ValueError("Jif3D frequencies must be positive and unique")
        dims = ("Frequency", "StationNumber")
        coordinate_dim = ("MeasNumber",) if "MeasNumber" in nc.dimensions else ("StationNumber",)
        xyz = [_array(nc, "MeasPos" + axis, coordinate_dim, units="m") for axis in "XYZ"]
        if any(len(values) != n for values in xyz):
            raise ValueError("Only co-located Jif3D measurements are supported")
        identity = np.broadcast_to(np.arange(n), (len(frequencies), n))
        for name in ("ExIndices", "EyIndices", "HIndices", "HxIndices", "HyIndices", "HzIndices"):
            if name in nc.variables and not np.array_equal(_array(nc, name, dims), identity):
                raise ValueError(f"Only co-located, frequency-independent Jif3D {name} are supported")
        if "RotationAngle" in nc.variables and np.any(_array(nc, "RotationAngle", ("StationNumber",)) != 0):
            raise ValueError("Jif3D RotationAngle must be zero; rotate the source explicitly first")
        if "C" in nc.variables:
            distortion = _array(nc, "C", ("StationNumber", "Celem"))
            if distortion.shape != (n, 4) or not np.all(distortion == [1, 0, 0, 1]):
                raise ValueError("Non-identity Jif3D distortion is outside observation conversion")
        names = [str(i + 1) for i in range(n)]
        if "Names" in nc.variables:
            if nc["Names"].dimensions != ("StationNumber",):
                raise ValueError("Jif3D Names must be a StationNumber string array")
            names = [str(name) for name in nc["Names"][:]]
        values, errors = {}, {}
        for group in (tuple(COMPONENTS)[:4], ("TX", "TY")):
            if not any(COMPONENTS[c] + suffix in nc.variables for c in group for suffix in ("_re", "_im")):
                continue
            for component in group:
                name = COMPONENTS[component]
                units = "Ohm" if component.startswith("Z") else ""
                real = _array(nc, name + "_re", dims, units=units)
                imag = _array(nc, name + "_im", dims, units=units)
                errors[component] = _array(nc, "d" + name, dims, units=units)
                if np.any(errors[component] < 0):
                    raise ValueError(f"Jif3D d{name} must be nonnegative")
                # Jif3D uses exp(+iwt); FEMTIC/Survey uses exp(-iwt).
                values[component] = real - 1j * imag
        if not values:
            raise ValueError("No Jif3D impedance or tipper observations")
        stations = tuple(Station(
            i + 1, names[i], None, None, None, float(xyz[0][i]), float(xyz[1][i]),
            float(xyz[0][i]) / 1000, float(xyz[1][i]) / 1000, float(xyz[2][i]) / 1000,
            tuple(ResponseSample(float(f), {c: complex(v[j, i]) for c, v in values.items()},
                                 {c: float(e[j, i]) for c, e in errors.items()}, frozenset(values))
                  for j, f in enumerate(frequencies)),
        ) for i in range(n))
    return Survey(stations, "ohm", "exp_minus_iwt", "jif3d")


def write_jif3d(survey: Survey, path: Path) -> None:
    """Write complete frequency/station grids; never fill absent observations."""
    frequencies = sorted({s.frequency_hz for station in survey.stations for s in station.samples}, reverse=True)
    present = set().union(*(s.active_components for station in survey.stations for s in station.samples))
    components = tuple(c for group in (tuple(COMPONENTS)[:4], ("TX", "TY"))
                       if present.intersection(group) for c in group)
    samples = [{s.frequency_hz: s for s in station.samples} for station in survey.stations]
    for station, rows in zip(survey.stations, samples):
        for f in frequencies:
            if f not in rows or not set(components).issubset(rows[f].active_components):
                raise ValueError(f"Jif3D requires a complete common frequency grid: {station.name}, {f:g} Hz; missing data are not filled")
    with _dataset()(path, "w", format="NETCDF4") as nc:
        n = len(survey.stations)
        nc.createDimension("StationNumber", n)
        nc.createDimension("MeasNumber", n)
        nc.createDimension("Frequency", len(frequencies))

        def put(name, data, dims, units, kind="f8"):
            variable = nc.createVariable(name, kind, dims)
            variable[:] = data
            variable.units = units

        put("Frequency", frequencies, ("Frequency",), "Hz")
        for axis, attr in zip("XYZ", ("model_x_km", "model_y_km", "surface_depth_km")):
            put("MeasPos" + axis, [getattr(s, attr) * 1000 for s in survey.stations], ("MeasNumber",), "m")
        put("RotationAngle", np.zeros(n), ("StationNumber",), "")
        names = nc.createVariable("Names", str, ("StationNumber",))
        names[:] = np.asarray([s.name for s in survey.stations], dtype=object)
        dims = ("Frequency", "StationNumber")
        identity = np.broadcast_to(np.arange(n), (len(frequencies), n))
        for name in ("ExIndices", "EyIndices", "HIndices", "HxIndices", "HyIndices", "HzIndices"):
            put(name, identity, dims, "", "i4")
        for component in components:
            name = COMPONENTS[component]
            values = np.asarray([[rows[f].values[component] for rows in samples] for f in frequencies])
            units = "Ohm" if component.startswith("Z") else ""
            put(name + "_re", values.real, dims, units)
            put(name + "_im", -values.imag, dims, units)
            put("d" + name, [[rows[f].standard_errors[component] for rows in samples] for f in frequencies], dims, units)
