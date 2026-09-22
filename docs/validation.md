# Validation

Run one block at a time in a fresh PowerShell session. Stop at any nonzero
exit code, failed manifest, or hash mismatch. Use new output directories.
The public stages are independent:

```text
mt2femtic validate --config CONFIG --output OUTPUT [--data DATA]
mt2femtic data --config CONFIG --output OUTPUT
mt2femtic mesh --config CONFIG --data DATA --output OUTPUT
mt2femtic convert --from FORMAT --to FORMAT --input INPUT --output OUTPUT
```

The original preparation commands write a log and their own manifests:

- `mt2femtic_validation_manifest.json`: preflight evidence.
- `mt2femtic_data_manifest.json`: FEMTIC inputs, projected stations, coordinate
  audit, QA figures, and source/output hashes.
- `mt2femtic_mesh_manifest.json`: DHEXA inputs and outputs, model mapping,
  generator identity, and product checks, including `MeshData.vtk`.

`convert` writes observations and `conversion.json`; see the
[conversion contract and examples](conversion.md). It does not rerun data
preparation or meshing.

For manual EDI preparation, use the
[manual workflow](../examples/manual_workflow/README.md) and inspect stages 00–04
separately. Stage 04 writes mesh-generator inputs.

Require CLI `status=passed` and `"status": "passed"` for every required entry
under the manifest's `stages` object. For `data` and `mesh`,
`run.resume=true` reuses output only when its configuration and hashes match;
`run.overwrite=true` replaces only matching MT2FEMTIC-owned output.

At zero azimuth, `X = north` and `Y = east`. Source elevation is positive up
in metres; FEMTIC depth is positive down in kilometres. Source
`exp(+i*omega*t)` is converted once to `exp(-i*omega*t)`. Exact selection does
not interpolate; log-linear selection does not extrapolate. See
[conventions](conventions.md) and the [file contract](femtic-input-contract.md).

## 1. Verify the package and install a fresh wheel

Run from an unmodified extracted source package. Verify its existing checksums
before building; do not regenerate a checksum manifest to bypass a mismatch.

```powershell
$Repo = (Get-Location).Path
$Example = Join-Path $Repo "examples\broken_hill"
$Review = Join-Path $Repo "work\manual-validation"
py -3.12 scripts\verify_package.py --root .
if ($LASTEXITCODE -ne 0) { throw "Package verification failed" }
if (Test-Path -LiteralPath $Review) { throw "Review directory already exists" }
New-Item -ItemType Directory -Path $Review | Out-Null
$env:PYTHONPATH = ""
$env:PYTHONDONTWRITEBYTECODE = "1"
py -3.12 -m venv "$Review\venv"
$Python = Join-Path $Review "venv\Scripts\python.exe"
& $Python -m pip install build
# The following invokes python -m build in the clean environment.
& $Python -m build --outdir "$Review\dist" $Repo
$Wheel = Get-ChildItem "$Review\dist" -Filter *.whl
& $Python -m pip install ($Wheel.FullName + "[conversion]")
Push-Location $Review
& $Python -c "import mt2femtic; print(mt2femtic.__version__); print(mt2femtic.__file__)"
```

Require version `0.1.0` imported from this review environment's `site-packages`.
Python dependencies may need internet access; Broken Hill inputs are bundled.

## 2. Test commands and small meshes

```powershell
& $Python -m mt2femtic --help
& $Python -m mt2femtic validate --help
& $Python -m mt2femtic data --help
& $Python -m mt2femtic mesh --help
& $Python -m mt2femtic convert --help
$env:MT2FEMTIC_DHEXA = Join-Path $Example "tools\makeDHexaMesh-v1.6.1"
& $Python -m unittest discover -s "$Repo\tests" -t $Repo -p "test_*.py" -v
```

The environment variable enables real v1.6.1 generator tests for small EDI and
ModEM cases, including native topography and model/output checks. Windows mesh
execution requires WSL2 Linux x86-64. No test may fail. Without that variable,
the real-generator test is skipped and mesh execution remains unverified.
The optional `conversion` extra enables native Jif3D NetCDF tests; without it,
those tests are skipped.

The [minimal example](../examples/minimal/README.md) provides editable data and
mesh configurations. Set its mesh generator path to `$env:MT2FEMTIC_DHEXA`,
version to `v1.6.1`, and SHA-256 to
`09d55ea38ba7136e1eda134ebf1d9de86d833982176bd14bdb02e1a75edad038`
before using the independent mesh commands.

## 3. Verify the complete Broken Hill data

These commands exercise the installed wheel with the bundled configurations:

```powershell
$Run = Join-Path $Review "broken-hill"
New-Item -ItemType Directory -Path $Run | Out-Null
Set-Location $Run
& $Python -m mt2femtic validate --config "$Example\config\edi.json" --output validation\edi
& $Python -m mt2femtic data --config "$Example\config\edi.json" --output data\edi
& $Python -m mt2femtic validate --config "$Example\config\modem.json" --output validation\modem
& $Python -m mt2femtic data --config "$Example\config\modem.json" --output data\modem
& $Python "$Example\scripts\convert_and_compare.py" --edi-output data\edi --modem-output data\modem --output comparison
& $Python "$Example\scripts\verify_outputs.py" --root . --write-checksums
& $Python -m mt2femtic validate --config "$Example\config\mesh.json" --data data\modem --output validation\mesh
```

Require 21 matched stations, 24 periods, 3006 matched complex values, no
unmatched keys, finite FEMTIC values, and `data_baseline: PASS`. The verifier
checks input/output hashes and compares all six FEMTIC data files with the
frozen `expected.json`. Inspect the reported EDI/ModEM coordinate offsets.

## 4. Reproduce H2

H2 preparation uses only ModEM-derived station coordinates. Keep the following
settings and the bundled baseline unchanged:

```powershell
$H2 = Join-Path $Run "h2"
& $Python "$Example\scripts\prepare_h2_mesh.py" `
  --observe data\modem\inversion_input\observe.dat `
  --base-meshgen "$Example\h2\base_meshgen.inp" --output-dir $H2 `
  --half-width-km 1.0 --max-edge-km 0.375 `
  --inner-half-width-km 0.5 --inner-max-edge-km 0.1875 `
  --parameter-level-limit 2 --expected-stations 21
$Tool = Join-Path $Example "tools\makeDHexaMesh-v1.6.0-extended"
$H2Wsl = (wsl.exe wslpath -a $H2.Replace("\", "/")).Trim()
$ToolWsl = (wsl.exe wslpath -a $Tool.Replace("\", "/")).Trim()
wsl.exe bash -lc "cd '$H2Wsl' && chmod +x '$ToolWsl' && '$ToolWsl' < meshgen.inp > meshgen.stdout 2> meshgen.stderr"
if ($LASTEXITCODE -ne 0) { throw "H2 generation failed" }
& $Python "$Example\scripts\verify_h2_mesh.py" --output-dir $H2 --expected "$Example\expected.json"
& $Python "$Example\scripts\verify_outputs.py" --root . --write-checksums
Pop-Location
```

Require `passed: true`, empty stderr, no mismatched hashes, 123983 nodes,
112664 elements, 96644 parameters, and 96643 active parameters. This verifies
historical output reproduction with the supplied binary; complete
source-to-binary provenance for extended v1.6.0 is not established.

The separate production mesh uses v1.6.1 and 129 x 120 x 62 ModEM base cells.
Execute it with the following optional command and inspect the mesh manifest,
model mapping, generator logs, and `MeshData.vtk`:

```powershell
& $Python -m mt2femtic mesh --config "$Example\config\mesh.json" --data "$Run\data\modem" --output "$Run\mesh"
```

Previous comparisons against private r5b/r10 meshes and server responses are
historical checks, not reproducible gates of this public package. This
procedure does not run a FEMTIC or ModEM inversion.

## 5. Check the built archive independently

```powershell
$Archive = Get-ChildItem "$Review\dist" -Filter *.tar.gz
$Extract = Join-Path $Review "extracted"
New-Item -ItemType Directory -Path $Extract | Out-Null
tar -xf $Archive.FullName -C $Extract
$Frozen = (Get-ChildItem $Extract -Directory).FullName
& $Python "$Frozen\scripts\verify_package.py" --root $Frozen
```

Require `passed: true`, no checksum errors, no internal development artifacts,
and no missing licenses. Repeat the Broken Hill checks with `$Example` set to
`$Frozen\examples\broken_hill` and a fresh output directory. Preserve logs and
manifests as the validation record. The source archive includes examples and
data; the wheel contains only the Python library.
