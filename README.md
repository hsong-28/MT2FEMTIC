# MT2FEMTIC

Prepare input files for [FEMTIC](https://github.com/yoshiya-usui/femtic) and
[ABIC](https://github.com/hsong-28/FEMTIC-DABIC), and convert observations between
FEMTIC, ModEM, and Jif3D within the documented
[format limits](docs/conversion.md).

## Install

Requires Python 3.12 or newer; validation used 3.12. The bundled mesh generators
require Linux x86-64 (WSL2 on Windows). From the source-package root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

On Linux, create the environment with `python3 -m venv .venv`, activate it
with `source .venv/bin/activate`, then run `python -m pip install .`.

## Quick start

Prepare the bundled small EDI example:

```powershell
mt2femtic validate --config examples/minimal/edi-data.json --output work/edi-check
mt2femtic data --config examples/minimal/edi-data.json --output work/edi-data
```

Require `status=passed` after each command. Inspect `work/edi-data` for the
FEMTIC input files, projected stations, QA figures, and processing manifest.
Use a fresh output directory for each run.

## Commands

```text
mt2femtic validate --config CONFIG --output OUTPUT [--data DATA]
mt2femtic data --config CONFIG --output OUTPUT
mt2femtic mesh --config CONFIG --data DATA --output OUTPUT
mt2femtic convert --from FORMAT --to FORMAT --input INPUT --output OUTPUT
```

`validate` checks inputs without running the mesh generator; `data` writes
FEMTIC inputs; `mesh` generates a DHEXA mesh; `convert` exchanges observations.
No command invokes another. Jif3D requires `python -m pip install ".[conversion]"`.
For `validate`, `--data` is required with a mesh configuration and omitted with
a data configuration. Paths inside JSON are relative to that file; CLI paths
are relative to the current directory. Use `mt2femtic COMMAND --help` for arguments.

## Examples

| Example | Purpose |
|---|---|
| [Broken Hill](examples/broken_hill/README.md) | Bundled 21-site EDI/ModEM data and verified historical H2 mesh reproduction |
| [Yellowstone](examples/yellowstone/README.md) | 98-site EDI download and preparation; not a reproduction of the published inversion |
| [Minimal](examples/minimal/README.md) | Small EDI/ModEM inputs and configuration templates for another survey |
| [Step-by-step workflow](examples/manual_workflow/README.md) | Prepare EDI data and mesh-generator inputs one stage at a time |

Before processing a new survey, set its CRS, origin, periods, units, time
convention, and error policy explicitly. At zero azimuth, X is north and Y is
east; FEMTIC depth is positive down in kilometres.

## Reference

- [Scientific conventions](docs/conventions.md)
- [FEMTIC input contract](docs/femtic-input-contract.md)
- [Validation and reproduction](docs/validation.md)
- [FEMTICPy compatibility](docs/femticpy-compatibility.md)

Code: [MIT License](LICENSE). Bundled data and tools retain their
[original licenses](THIRD_PARTY_NOTICES.md). Citation: [CITATION.cff](CITATION.cff).
Contact: han.song@tu-berlin.de.
