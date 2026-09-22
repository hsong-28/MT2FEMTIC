# Observation conversion

`convert` exchanges impedances and tippers between FEMTIC, ModEM, and Jif3D.
It preserves active values, standard errors, frequencies, and local coordinates.
It does not select periods, interpolate, rotate, apply error floors, or convert
models, meshes, distortion parameters, or inversion settings.

```powershell
mt2femtic convert --from modem --to femtic --input examples/broken_hill/input/modem/BH_31.dat --output work/bh-femtic
mt2femtic convert --from femtic --to modem --input work/bh-femtic --output work/bh-modem
```

The input may be a file or an output directory from `convert`. The output must
be a new directory. It contains the observation file and `conversion.json`.
Keep both: the checksum-checked metadata preserves station names, geographic
coordinates, and depths that FEMTIC `observe.dat` cannot store. FEMTIC output
is observation data only; mesh refinement, topography, and distortion inputs
still belong to data/mesh preparation.

For a standalone FEMTIC file, declare its common surface depth explicitly:

```powershell
mt2femtic convert --from femtic --to modem --input observe.dat --surface-depth-m 0 --output work/modem
```

This declaration is positive down in metres. For sites at differing elevations,
use retained conversion metadata; a single depth cannot describe their geometry.
Unknown latitude/longitude and origin are written as zero placeholders in ModEM,
as in Jif3D's ModEM writer. They remain unknown in the metadata. Local X/Y/Z are
authoritative; this command does not establish or change a geographic CRS.

## Supported contracts

| Format | File | Impedance / time convention | Coordinates |
|---|---|---|---|
| FEMTIC | `observe.dat`, MT and optional VTF | ohm, `exp(-iwt)` | X/Y in km; surface depth is external |
| ModEM | `survey.dat`, list-format blocks | read declared ohm or `(mV/km)/nT` and either sign; write field units, `exp(+iwt)` | X north, Y east, Z down, metres |
| Jif3D | `survey.nc`, native MT/Tipper NetCDF | ohm, `exp(+iwt)` | north/east/down, metres |

All three use the same unrotated local frame. ModEM blocks must have complete
headers, consistent units/sign/origin, and correct period/station counts.
FEMTIC MT and VTF must be co-located and linked by their magnetic station ID.
Nonzero rotations, tangential electric fields, and remote-reference geometry
are outside this first implementation; unsupported declared cases fail.
Confirm that external FEMTIC files use the declared frame: their headers do
not encode a CRS or model-axis azimuth.

The normalization is `Z_ohm = 1000 * mu0 * Z_field`, with `mu0 = 4*pi*1e-7`.
Errors receive the same positive scale. Changing the time convention
conjugates both impedance and tipper; it does not change their errors.
ModEM period is the reciprocal of frequency. No coordinate swap or vertical
sign change is needed between the shared local frames; only metres/kilometres
are converted. These conversions write 17 significant digits, while the
existing `data` command retains its established 12-digit output.

The older `data` ModEM adapter calls its seventh column `elevation_m`.
`convert` explicitly interprets the standard ModEM column as positive-down Z;
the original data-preparation behavior is unchanged. Broken Hill has Z=0.

## Jif3D

Install the optional NetCDF dependency:

```powershell
python -m pip install ".[conversion]"
mt2femtic convert --from modem --to jif3d --input examples/minimal/input/modem/complete.dat --output work/jif3d
mt2femtic convert --from jif3d --to femtic --input work/jif3d --output work/femtic
```

The bundled complete file is a synthetic 100 ohm-m half-space at 1 Hz,
with zero diagonal impedance and tipper. Its geographic zeros are placeholders.

A separate [upstream Jif3D fixture](../tests/fixtures/jif3d/README.md) includes
the native `testJ` sample: one station, five frequencies, 20 complex impedances,
and 10 complex tippers. To try it with the installed command:

```powershell
mt2femtic convert --from jif3d --to femtic --input tests/fixtures/jif3d/impedance.nc --output work/testj-femtic
mt2femtic convert --from femtic --to modem --input work/testj-femtic --output work/testj-modem
mt2femtic convert --from modem --to jif3d --input work/testj-modem --output work/testj-returned
```

Use `tipper.nc` and fresh output directories to check the tipper group.
Both routes and every intermediate format are covered by the regression test.
Returned NetCDF files also passed the upstream C++ MT/tipper readers in the
2026-09-22 validation, with identical numerical arrays.

The supported native schema uses `Frequency`, `StationNumber`, `MeasPosX/Y/Z`,
`Zxx_re/im` through `Zyy_re/im`, `dZxx` through `dZyy`, and optional
`Tx_re/im`, `Ty_re/im`, `dTx`, `dTy`. Array order is frequency, then station.
Electric and magnetic measurement indices must identify the same station at
every frequency. Non-identity distortion matrices are rejected. Error arrays
are required; omitted errors are not invented. Impedance and tipper variables
can share one file, readable separately by the native MT and tipper readers.

Jif3D requires a complete common frequency grid for every present response
group: four impedance components and/or both tipper components. This converter
rejects missing cells instead of filling data or assigning artificial errors.
Broken Hill's 3,006 active complex observations include incomplete tipper cells,
so its complete FEMTIC/ModEM round trip is supported, but a full Jif3D export
is rejected. Choosing a complete subset is a separate data-selection decision.

## Traceability and validation

The implementation follows the upstream readers/writers inspected on
2026-09-22; it does not copy their implementation:

- [Jif3D ReadWriteImpedances.h](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/MT/ReadWriteImpedances.h): units, axes, frequency/station ordering, paired errors.
- [Jif3D ReadWriteImpedances.cpp](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/MT/ReadWriteImpedances.cpp): NetCDF fields and ModEM time/unit conventions.
- [Jif3D ReadWriteTitanData.cpp](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/MT/ReadWriteTitanData.cpp): MT measurement indices and ordinary co-located fallback.
- [Jif3D MTEquations.cpp](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/MT/MTEquations.cpp): half-space impedance `sqrt(i*omega*mu0/sigma)`, independently fixing the positive-time phase.
- [ModEM read/write documentation](https://github.com/magnetotellurics/ModEM-Tools/blob/main/Examples/Read_Write_Data_Example.MD): impedance and vertical-component block types.

`conversion.py` handles ASCII formats and common checks; `jif3d_io.py` handles
the native NetCDF subset. `tests/test_conversion.py` checks known units/signs,
nonzero depths, native array fields, missing-data rejection, all six conversion
directions, and the full Broken Hill FEMTIC/ModEM round trip. Each conversion
also rereads its output and checks observations within floating-point roundoff.
This validates observation interchange, not forward modelling or inversion.
