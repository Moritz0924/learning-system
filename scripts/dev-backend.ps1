$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$env:PYTHONPATH = "$Root;$Root\src"

& $Python -m alembic -c backend\alembic.ini upgrade head
& $Python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
