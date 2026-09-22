# Minimal inputs

The EDI and ModEM configurations use the small inputs bundled in `input/`.
Run from the repository root after installing MT2FEMTIC:

```powershell
mt2femtic validate --config examples/minimal/edi-data.json --output work/minimal/validate-edi
mt2femtic data --config examples/minimal/edi-data.json --output work/minimal/data-edi
mt2femtic validate --config examples/minimal/modem-data.json --output work/minimal/validate-modem
mt2femtic data --config examples/minimal/modem-data.json --output work/minimal/data-modem
```

Require `status=passed`. These synthetic inputs exercise the file formats;
they are not a field interpretation. Before using `dhexa-mesh.json`, set its
generator path, version, and SHA-256. See [validation](../../docs/validation.md)
for a small mesh check using the generator bundled with Broken Hill.

`input/modem/complete.dat` is a complete, synthetic 100 ohm-m half-space for
the [FEMTIC/ModEM/Jif3D conversion example](../../docs/conversion.md).

## Adapt to another survey

Copy this directory and replace its inputs. Set the source path, CRS, origin,
axis azimuth, vertical datum, target periods, units, time convention, and error
floors in the data configuration. Paths resolve relative to the JSON file.
For EDI inventory and station naming, see [conventions](../../docs/conventions.md).
Set `selection.impedance_error_floor_fraction` to `0.0` when accepted source
errors must be preserved. A ModEM model belongs in the mesh configuration.

## Mesh existing FEMTIC data

Set the generator identity and mesh settings in a copy of `dhexa-mesh.json`.
The data directory can be an MT2FEMTIC data output or an external directory
containing `observe.dat`, `obs_site.dat`, and `distortion_iter0.dat`:

```powershell
mt2femtic validate --config path/to/mesh.json --data path/to/femtic-data --output work/mesh-check
mt2femtic mesh --config path/to/mesh.json --data path/to/femtic-data --output work/mesh
```

On Windows, obtain the generator SHA-256 with
`(Get-FileHash -Algorithm SHA256 path/to/makeDHexaMesh).Hash.ToLower()`.
Use `mesh.observation_refinement=from_data` for data-provided station rules or
`none` for an unrefined base mesh. Air spacing supports `geometric` and, with
ModEM axes, `modem_fixed_height`. Review the
[FEMTIC input contract](../../docs/femtic-input-contract.md) before changing
station selectors. Mesh outputs are separate from the read-only data input.
