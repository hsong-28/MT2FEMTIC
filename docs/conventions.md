# Scientific conventions

All conventions are explicit configuration inputs and are recorded in the
data manifest and coordinate audit.

The observation-only `convert` command follows the separate
[conversion contract](conversion.md), including standard ModEM positive-down Z
and Jif3D's positive-time NetCDF convention.

## EDI inventory and station identity

- `source.edi_list_file` preserves the listed file order and must declare the
  same count as its nonempty filename rows. Use `null` to select files with
  `source.edi_pattern` in natural filename order.
- `source.station_name_source="edi_metadata"` uses `DATAID`, then a supported
  station-name metadata field, then the file stem. Use `file_stem` when the
  filename is the authoritative station identity.
- Inventory entries must resolve inside the configured EDI directory. Missing
  files, duplicates, count mismatches, and duplicate station names are errors.

## Horizontal coordinates

- Geographic EDI locations are projected from `geographic_crs` to
  `projected_crs` with `always_xy=true`.
- The configured easting and northing origin is subtracted before rotation.
- At `model_axis_azimuth_deg = 0`, FEMTIC model X is north and model Y is east.
- Data preparation requires `model_axis_azimuth_deg = 0`. Nonzero model-axis
  azimuths and nonzero ModEM header orientations are rejected: rotating station
  coordinates alone would leave responses in a different frame. Response and
  error rotation is not implemented. EDI responses must already use north/east axes.
- FEMTIC model coordinates are kilometres.
- ModEM north/east offsets are authoritative when
  `modem_axis_convention = "north_east"`; geographic columns are retained for
  provenance but do not replace those offsets.
- The configured round-trip tolerance is in metres.

Changing the CRS or origin changes station and topography model
coordinates. Confirm `projection/stations_projected.csv`,
`coordinate_convention_audit.json`, and `qa/station_topography.png` before
meshing.

## Vertical coordinates

EDI source elevation is positive upward in metres. FEMTIC and DHEXA depth is
positive downward in kilometres relative to
`vertical_datum_elevation_m`:

```text
depth_km = (vertical_datum_elevation_m - elevation_m) / 1000
```

ModEM data preparation requires `source.modem_vertical_coordinate`:

- `depth_m`: standard positive-down Z in the existing model frame;
  `depth_km = Z_m / 1000`. No datum shift is applied and elevation is left unknown.
- `elevation_m`: explicit compatibility with historical list files whose seventh
  column stores positive-up elevation; use the elevation formula above.

There is no inferred default. Set this field before rerunning an older ModEM
configuration, even if all Z values are zero. For EDI, omit it or use `null`.
The declaration is recorded in the resolved configuration and source metadata.

Normalized mesh topography files contain `model_x_km model_y_km depth_km`.

## Fourier sign

The internal and output convention is `exp(-i*omega*t)`. An
`exp(+i*omega*t)` source is conjugated exactly once. EDI convention metadata
is read from supported `SIGNCONVENTION` or processing notes; ModEM convention
metadata is read from the data header. Missing or conflicting metadata stops
the command unless `allow_time_convention_override=true` explicitly records
the configured source convention.

## Response units

- Impedance already in ohms is unchanged.
- `(mV/km)/nT` impedance is multiplied by `1000 * mu0` to obtain ohms.
- VTF is dimensionless.
- Complex values and their standard errors must be finite.
- ModEM file units must agree with `source.impedance_unit`. A time-convention
  override does not override units or orientation. Full headers use the same
  type, origin and count checks as `convert`; historical abbreviated impedance
  headers remain supported by `data` in the declared north/east frame.

The shared checks are in `modem_adapter.read_modem_header`; vertical values
are normalized by `read_modem_data` and `conventions.project_station`.
The native Jif3D `ReadImpedancesFromModEM`/`WriteImpedancesToModEM` routines
preserve station Z in metres; see the [conversion sources](conversion.md#traceability-and-validation).
Explicit elevation compatibility and rejection of unsupported rotations are
MT2FEMTIC policies. `test_modem_adapter`, `test_conventions` and
`test_data_pipeline` check nonzero depth, datum handling, metadata conflicts
and rejection before publishing FEMTIC observations.

## Frequency selection and errors

`frequency_policy="exact"` matches requested periods within the declared
relative tolerance and never interpolates. `frequency_policy="log_linear"`
interpolates each available complex component and standard error linearly in
log frequency; it never extrapolates.

The impedance error floor is the configured fraction times the geometric mean
of `|Zxy|` and `|Zyx|`. The VTF floor is an absolute value. VTF components
beyond `max_vtf_period_s` are inactive. Missing FEMTIC components are written
with negative error markers.

Set an error floor to zero when the source already contains the accepted final
errors and those values must be preserved. Source errors are still validated
and serialized; zero disables only the additional floor.
