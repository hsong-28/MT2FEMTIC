param(
    [string]$RunName = "broken-hill-validation",
    [switch]$RunMesh,
    [switch]$RunH2
)

$ErrorActionPreference = "Stop"
$Example = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Repository = (Resolve-Path -LiteralPath (Join-Path $Example "..\..")).Path
$Run = Join-Path $Repository ("work\" + $RunName)

if (Test-Path -LiteralPath $Run) {
    throw "Run destination already exists: $Run"
}

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = Join-Path $Repository "src"
$Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

function Invoke-Checked([string]$Name, [scriptblock]$Action) {
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Invoke-Checked "local package check" {
    & $Python -c "import sys, mt2femtic; print(sys.executable); print(mt2femtic.__version__); print(mt2femtic.__file__)"
}
New-Item -ItemType Directory -Path $Run | Out-Null

$CliArgs = @("-m", "mt2femtic")
$ModemConfig = Join-Path $Example "config\modem.json"
$EdiConfig = Join-Path $Example "config\edi.json"
$ModemData = Join-Path $Run "data\modem"
$EdiData = Join-Path $Run "data\edi"

foreach ($Source in @("modem", "edi")) {
    $Config = if ($Source -eq "modem") { $ModemConfig } else { $EdiConfig }
    $Data = if ($Source -eq "modem") { $ModemData } else { $EdiData }
    Invoke-Checked "validate $Source" {
        & $Python @CliArgs validate --config $Config --output (Join-Path $Run "validation\$Source")
    }
    Invoke-Checked "prepare $Source" {
        & $Python @CliArgs data --config $Config --output $Data
    }
}

Invoke-Checked "compare EDI and ModEM" {
    & $Python (Join-Path $Example "scripts\convert_and_compare.py") `
        --modem-output $ModemData `
        --edi-output $EdiData `
        --output (Join-Path $Run "comparison")
}
Invoke-Checked "verify data outputs" {
    & $Python (Join-Path $Example "scripts\verify_outputs.py") --root $Run --write-checksums
}
Invoke-Checked "validate production mesh" {
    & $Python @CliArgs validate `
        --config (Join-Path $Example "config\mesh.json") `
        --data $ModemData `
        --output (Join-Path $Run "validation\mesh")
}

if ($RunMesh) {
    Invoke-Checked "run production mesh" {
        & $Python @CliArgs mesh `
            --config (Join-Path $Example "config\mesh.json") `
            --data $ModemData `
            --output (Join-Path $Run "mesh")
    }
}

if ($RunH2) {
    $H2 = Join-Path $Run "h2"
    Invoke-Checked "prepare H2" {
        & $Python (Join-Path $Example "scripts\prepare_h2_mesh.py") `
            --observe (Join-Path $ModemData "inversion_input\observe.dat") `
            --base-meshgen (Join-Path $Example "h2\base_meshgen.inp") `
            --output-dir $H2 `
            --half-width-km 1.0 `
            --max-edge-km 0.375 `
            --inner-half-width-km 0.5 `
            --inner-max-edge-km 0.1875 `
            --parameter-level-limit 2 `
            --expected-stations 21
    }
    $H2Wsl = (wsl.exe wslpath -a $H2.Replace("\", "/")).Trim()
    $Tool = Join-Path $Example "tools\makeDHexaMesh-v1.6.0-extended"
    $ToolWsl = (wsl.exe wslpath -a $Tool.Replace("\", "/")).Trim()
    wsl.exe bash -lc "cd '$H2Wsl' && chmod +x '$ToolWsl' && '$ToolWsl' < meshgen.inp > meshgen.stdout 2> meshgen.stderr"
    if ($LASTEXITCODE -ne 0) {
        throw "H2 mesh generation failed with exit code $LASTEXITCODE"
    }
    Invoke-Checked "verify H2" {
        & $Python (Join-Path $Example "scripts\verify_h2_mesh.py") `
            --output-dir $H2 `
            --expected (Join-Path $Example "expected.json")
    }
}

Invoke-Checked "write final checksums" {
    & $Python (Join-Path $Example "scripts\verify_outputs.py") --root $Run --write-checksums
}

Write-Output "status=PASS"
Write-Output "run=$Run"
