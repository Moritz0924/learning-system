param(
    [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$TempDir = Join-Path $Root ".tmp"
New-Item -ItemType Directory -Force $TempDir | Out-Null
$env:TMP = (Resolve-Path $TempDir).Path
$env:TEMP = (Resolve-Path $TempDir).Path

if (-not $SkipCompile) {
    & $Python -m compileall backend src tests -q
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $Python -m pytest @args
exit $LASTEXITCODE
