# Step-by-step workflow

Stages 00 through 04 are implemented. Run each command separately
from the repository root with the installed Python environment activated.
First copy this template to your survey directory, place its EDI files in
`0-EDI`, and edit `survey.json` for that survey. Inspect each JSON report
before continuing.

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$SurveyRoot = ".\surveys\my-survey"
python scripts\00_check_input.py --root $SurveyRoot
python scripts\01_read_edi.py --root $SurveyRoot
python scripts\02_project_sites.py --root $SurveyRoot
python scripts\03_select_data.py --root $SurveyRoot
python scripts\04_write_meshgen.py --root $SurveyRoot
```

The survey root must contain `survey.json` and `0-EDI`. Stage 01 creates
`1-DataOri`; stage 02 creates `2-Projection`; stage 03 creates
`3-DataSelected4Inv`; stage 04 creates `4-MeshGeneration`. Each stage refuses an existing output directory and
verifies the source and previous manifests before publishing its output.

`stations_projected.csv` is the explicit coordinate record. At zero azimuth,
`model_x_km` is north and `model_y_km` is east. `surface_depth_km` is positive
downward from the configured vertical datum. The compatibility file
`site_xyz.dat` preserves the legacy column order:

```text
model_y_km model_x_km elevation_m station_id station_name
```

Stage 03 writes `observe.dat`, `obs_site.dat`, `distortion_iter0.dat`, and its
manifest. It applies the declared exact or log-linear period policy without
extrapolation, applies the declared impedance and VTF error floors, and retains
the validated negative error marker for missing components.

Before stage 04, copy `mesh.json` from this directory to the survey root and
set the mesh extents, air layers, resistivities, and generator identity. The
declared generator path and all referenced model and topography files must be
inside the survey root. Stage 04
checks the Stage 03 hashes, validates every station against the X/Y mesh bounds,
and writes `meshgen.inp`, `obs_site.dat`, an optional topography file, and
`stage-manifest.json`. It records the generator as `declared_not_verified` and
does not execute DHEXA. The resulting directory therefore contains generator
inputs only; it must not contain `mesh.dat`, `MeshData.vtk`, or execution logs.
