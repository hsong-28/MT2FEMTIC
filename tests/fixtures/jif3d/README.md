# Jif3D upstream MT fixture

One station (`go_01`), five frequencies (10400, 8800, 7200, 6000, 5200 Hz),
20 complex impedances, and 10 complex tippers. This is Jif3D's I/O unit-test
sample, not a forward or inversion tutorial. Retrieved 2026-09-22.

## Origin

- [testJ.mtt](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/testJ.mtt)
  and [testJ.j](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/testJ.j):
  unmodified upstream files; both responses had SVN ETag revision 896.
- [read_write_J_test](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/MT/test_ReadWriteImpedances.cpp)
  compares these files using 0.001 percent relative tolerance. Their rounded
  frequencies/impedances/errors agree here within 3.2e-6 relative difference.
- [Upstream GPL v3](https://svn.code.sf.net/p/jif3d/jif3dsvn/trunk/jif3D/gpl-v3.txt)
  is retained as `LICENSE`. These fixtures retain their upstream terms.

| File | SHA-256 |
|---|---|
| testJ.mtt | `6bba9f4312b430acad00ffd80cda7d8155b1fd3c231e2e1980ac8ff6533d8658` |
| testJ.j | `87f751465b8e7d6463eec4f9c7b95d19ad7fbbea49ae232eb2770c9a2d689145` |
| impedance.nc | `db9a3aed3d9efeed0eb0f25dbc03d6da2febb373dc9d41665b552f4209356f86` |
| tipper.nc | `16cc384d72e7e3265ee64a77cde658800a963bed651dd0ea4c369fc7e66234c8` |

## Preparation and contracts

The NetCDF fixtures were produced by compiling unmodified Jif3D I/O sources
with GCC 11.4, Boost 1.74, NetCDF C 4.8.1, and NetCDF C++ 4.3.1:

| Upstream source | SHA-256 |
|---|---|
| MT/ReadWriteImpedances.cpp | `1b16ee0fb362c1911a52af0d42d9e82a6f885f9619d4e12456aa70d804b242c4` |
| MT/ReadWriteTitanData.cpp | `7470af52a9d33fef39b0a5fe0a6d2314c38389a9d9020904e039953dda8a0821` |
| Global/NetCDFPortHelper.cpp | `ccd7b4cc7a935cf2a6794e2634b4faeb3b1c39794dac22d2268bf7d054afc3f6` |

`ReadImpedancesFromMTT` reads `testJ.mtt`; `WriteTitanDataToNetCDF` writes
`impedance.nc`, and `WriteTipperToNetCDF` writes `tipper.nc`. MTT impedance
values and errors are multiplied by `4*pi*1e-4` to obtain ohms. Tippers and
their errors are unchanged. Native NetCDF uses `exp(+iwt)`.

The station is assigned local north/east coordinates `(0, 0)` metres. This
is an explicit local-origin choice, not a projection of latitude/longitude.
The J-file elevation of 119 m gives positive-down Z = -119 m. Its latitude
53.3392 and longitude 16.3278 remain recorded only in `testJ.j`. Rotation
is zero, all measurement indices are zero, and distortion is identity.

## Reproduce the conversion check

From the package root, after installing `.[conversion]`:

```powershell
python -m unittest tests.test_conversion.ConversionTests.test_upstream_jif3d_example_and_all_six_directions -v
```

The test reads the bundled native files, independently derives expected
values/errors from the original MTT numbers, and checks every intermediate
file through both `Jif3D -> FEMTIC -> ModEM -> Jif3D` and
`Jif3D -> ModEM -> FEMTIC -> Jif3D`. No download or Jif3D build is needed.

In the 2026-09-22 native validation, `ReadTitanDataFromNetCDF` and
`ReadTipperFromNetCDF` also read the returned files successfully. Frequencies,
coordinates, responses, errors, rotations, and measurement indices were
identical to the native references; station names matched. The native legacy
impedance schema passed the same two routes. This C++ check was a separate
validation run; the Python regression test does not rerun it. No forward
modelling or inversion was performed.
