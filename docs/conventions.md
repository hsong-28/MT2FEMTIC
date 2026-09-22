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
- Positive azimuth rotates the horizontal axes through the single transform
  implemented in `conventions.py`.
- FEMTIC model coordinates are kilometres.
- ModEM north/east offsets are authoritative when
  `modem_axis_convention = "north_east"`; geographic columns are retained for
  provenance but do not replace those offsets.
- The configured round-trip tolerance is in metres.

Changing the CRS, origin, or azimuth changes station and topography model
coordinates. Confirm `projection/stations_projected.csv`,
`coordinate_convention_audit.json`, and `qa/station_topography.png` before
meshing.

## Vertical coordinates

Source elevation is positive upward in metres. FEMTIC and DHEXA depth is
positive downward in kilometres relative to
`vertical_datum_elevation_m`:

```text
depth_km = (vertical_datum_elevation_m - elevation_m) / 1000
```

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
