# Yellowstone/Snake River Plain EDI example

This data-preparation example reconstructs the retained 98-site USArray
selection and the global processing settings used for an earlier
Yellowstone/Snake River Plain FEMTIC case. It downloads the currently published
EarthScope EDI files and runs the reviewable single-survey stages 00 through 03.

It does not claim byte identity with the retained legacy `observe.dat`. The
current archive provides `NVM11.2010`, whereas the retained station inventory
used a separate `NVM11.2011` response. The legacy file also contains additional
station-period and component quality-control masks whose original selection
record was not found. The retained DHEXA mesh cannot be regenerated because its
original generator configuration and topography input were not found. For
these reasons this example stops before Stage 04 and does not reproduce the
published inversion model.

## Prepare one independent folder

Run from the repository root with the installed Python environment activated:

```powershell
$SurveyRoot = ".\work\yellowstone"
if (Test-Path -LiteralPath $SurveyRoot) { throw "Survey directory already exists" }
New-Item -ItemType Directory -Path .\work -Force | Out-Null
Copy-Item .\examples\yellowstone $SurveyRoot -Recurse
python "$SurveyRoot\download_edi.py" --root $SurveyRoot
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

The downloader verifies every file against `source-files.json`. The completed
folder contains the configuration, ordered inventory, provenance, downloader,
and all 98 EDI inputs.

## Run and inspect each stage

```powershell
python .\scripts\00_check_input.py --root $SurveyRoot
python .\scripts\01_read_edi.py --root $SurveyRoot
python .\scripts\02_project_sites.py --root $SurveyRoot
python .\scripts\03_select_data.py --root $SurveyRoot
```

Stage 00 is a read-only inventory check and does not emit a status field. Stop
if it does not report 98 files. For Stages 01 through 03, stop if
`"status": "passed"` is absent. The expected current-archive checkpoints are:

| Stage | Check |
|---|---|
| 00 | 98 EDI files in the declared order |
| 01 | 98 stations, 2938 source samples, output in ohm and `exp(-i*omega*t)` |
| 02 | 98 projected stations, X north and Y east at zero azimuth |
| 03 | 1370 MT samples and 1176 VTF samples |

With the file hashes in `source-files.json`, Stage 03 must produce:

```text
observe.dat             14c8a7eb44a11514c35cb42ae3d6b9cb29674bdcd1185d9335b4aaf180db50e8
obs_site.dat            91964869e6ed273d53da403fa1ff96f456b79aad0957fd026ea6802ed65a30e7
distortion_iter0.dat    557d3d4260db1e6eda95b76988d13a7211d7b603e194ad7a0c48765af0bfafd5
```

The selected periods span 9.142857142857142 to 18724.568525086335 s. Stage 03
uses a 5% impedance error floor, an absolute VTF error floor of 0.03, and
removes VTF components above 10000 s. Inspect
`3-DataSelected4Inv/stage-manifest.json` and `observe.dat` before using the data
with a new mesh. Observation refinement is set to level 0 because the retained
mesh-generation settings were not recoverable.

See [source provenance](SOURCE_PROVENANCE.md) for citations and the exact
reproducibility boundary.
