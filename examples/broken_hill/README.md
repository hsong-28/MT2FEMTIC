# Broken Hill example

All survey inputs, configurations, DHEXA executables, and validation baselines
are bundled here. Python 3.12 and the package dependencies must be installed;
mesh execution requires Linux x86-64 (WSL2 on Windows).

```text
broken_hill/
|-- input/edi/             21 original EDI files
|-- input/modem/           BH_31.dat and BH_31_NLCG_030.rho
|-- config/               edi.json, modem.json, mesh.json
|-- h2/base_meshgen.inp
|-- tools/                two DHEXA executables and their license
|-- scripts/              comparison, H2 preparation, and verification
|-- expected.json
|-- SOURCE_PROVENANCE.md
|-- README.md
`-- run.ps1
```

## Run the data workflow

From the repository root, activate the Python environment used for installation
(on Windows: `.\.venv\Scripts\Activate.ps1`). The runner uses its `python`:

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\broken_hill\run.ps1 -RunName manual-broken-hill
```

This reads the bundled inputs directly and writes results to
`work/manual-broken-hill`. It runs EDI and ModEM validation and data preparation,
compares both routes, checks the six FEMTIC files against `expected.json`, and
preflights the production mesh. A run directory must not already exist.

## Run and inspect each data command

Use a fresh PowerShell session from the repository root. Stop after any nonzero
exit code or failed manifest; inspect each output before the next command.
Activate the installed environment before running these commands.

```powershell
$Example = (Resolve-Path .\examples\broken_hill).Path
$Run = [System.IO.Path]::GetFullPath(".\work\manual-broken-hill-steps")
$env:PYTHONPATH = (Resolve-Path .\src).Path
if (Test-Path -LiteralPath $Run) { throw "Run directory already exists" }
New-Item -ItemType Directory -Path $Run | Out-Null
Push-Location $Run

python -m mt2femtic validate --config "$Example\config\modem.json" --output validation\modem
python -m mt2femtic data --config "$Example\config\modem.json" --output data\modem
python -m mt2femtic validate --config "$Example\config\edi.json" --output validation\edi
python -m mt2femtic data --config "$Example\config\edi.json" --output data\edi

python "$Example\scripts\convert_and_compare.py" --modem-output data\modem --edi-output data\edi --output comparison
python "$Example\scripts\verify_outputs.py" --root . --write-checksums
python -m mt2femtic validate --config "$Example\config\mesh.json" --data data\modem --output validation\mesh
Pop-Location
```

Require 21 matched stations, 24 target periods, 3006 matched complex values,
no unmatched records, and `data_baseline: PASS`. Inspect coordinate offsets:
EDI and ModEM station positions are not assumed identical.

## Generate and verify H2

The historical H2 workflow uses the ModEM-derived station coordinates and the
bundled extended v1.6.0 generator:

```powershell
powershell -ExecutionPolicy Bypass -File .\examples\broken_hill\run.ps1 -RunName manual-broken-hill-h2 -RunH2
```

Require `passed: true`, no hash mismatches, empty generator stderr,
123983 nodes, 112664 elements, 96644 parameters, and 96643 active parameters.
See [validation](../../docs/validation.md) for separate preparation, execution,
and verification commands. Complete source-to-binary provenance for this
historical executable is not established.

The separate production mesh uses v1.6.1 and the ModEM model axes. Run it by
adding `-RunMesh` to `run.ps1`; it has 129 x 120 x 62 base cells. It is distinct
from the locally refined historical H2 mesh.

## Scientific contracts

- Source impedance is `(mV/km)/nT` with `exp(+i*omega*t)`; conversion to the
  FEMTIC `exp(-i*omega*t)` convention conjugates it once.
- At zero azimuth, FEMTIC X is north and Y is east. Elevation is positive up in
  metres; FEMTIC depth is positive down in kilometres.
- ModEM uses exact period selection and retains its supplied impedance errors.
- EDI uses log-frequency linear interpolation without extrapolation and a 5%
  impedance error floor. Its time-convention override is explicit.

This example prepares data and meshes. It does not reproduce an inversion.
