# FEMTIC input contract

The `data` command writes three required ASCII files under `inversion_input/`:

```text
observe.dat
obs_site.dat
distortion_iter0.dat
```

The mesh command accepts this native layout together with
`mt2femtic_data_manifest.json`, or an external directory containing the same
three files at its root. A directory containing both layouts is rejected as
ambiguous. Native output hashes are verified before parsing. External inputs
are hashed and audited without being modified.

## `observe.dat`

`MT` is required and must contain at least one station. `VTF` is optional.
`END` must be the last nonempty line.

The canonical MT2FEMTIC station headers are:

```text
MT:  electric_station_id magnetic_station_id 0 model_x_km model_y_km
VTF: magnetic_station_id magnetic_station_id 1 model_x_km model_y_km
```

For surface DHEXA stations, `0` selects the lower earth element for MT electric
fields and `1` selects the upper air element for VTF magnetic fields. The
reader rejects any other owner-element selector. A six-column MT header may
also specify FEMTIC's electric-field selector (`0` horizontal or `1`
tangential); DHEXA calculations use horizontal electric fields.

For external compatibility, the reader accepts four-column MT/VTF headers
without an individual selector, five-column headers with one selector, and
six-column MT headers with owner-element and electric-field selectors. IDs
are positive integers, selectors are integers, and coordinates are always the
final two columns.

Each station header is followed by a nonnegative sample count. MT rows contain
17 finite numeric columns: frequency, eight response values, and eight errors.
VTF rows contain 9 columns: frequency, four response values, and four errors.
Frequencies are positive and unique within a station. Paired real and
imaginary errors must match.

## `obs_site.dat`

The first row is the MT station count. Each station contains one finite
`model_x_km model_y_km depth_km` row, a positive refinement-rule count, and
that many `radius_km level weight` rows. Radius and weight are positive; level
is a nonnegative integer. A single `0` terminates the file.

The data configuration names these rules `femtic.observation_refinement`.
They control mesh sizing around stations and are independent of the owner
element selectors in `observe.dat`.

MT coordinates match observation sites in row order within
`station_coordinate_tolerance_km`. Each optional VTF location must match an
observation-site location within the same tolerance.

Set `mesh.observation_refinement` to `from_data` to use these station rules, or
to `none` to generate the unrefined base mesh. The latter writes an empty
generator-only `obs_site.dat` inside the mesh output without changing the data
package.

## `distortion_iter0.dat`

The first row is the MT station count. Every following row has six finite
numeric columns. The first column is a unique positive electric station ID,
and the ID set must exactly match the MT section.
