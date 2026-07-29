param(
    [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $GitCommonDirs = @(& git rev-parse --path-format=absolute --git-common-dir 2>$null)
        $GitExitCode = $LASTEXITCODE
        $GitCommonDir = $GitCommonDirs[0]
        if ($GitExitCode -eq 0 -and $GitCommonDir) {
            $SharedPython = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($GitCommonDir.Trim()))) ".venv\Scripts\python.exe"
            if (Test-Path $SharedPython) {
                $Python = $SharedPython
            }
        }
    }

    if (-not (Test-Path $Python)) {
        $Python = "python"
    }
}

$TaskTempRoot = Join-Path $Root ".tmp"
$RunId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$RunTempDir = Join-Path $TaskTempRoot ("t-{0}-{1}" -f $PID, $RunId)
$OriginalTmp = $env:TMP
$OriginalTemp = $env:TEMP
$ExitCode = 0

try {
    New-Item -ItemType Directory -Force $RunTempDir | Out-Null
    $env:TMP = (Resolve-Path $RunTempDir).Path
    $env:TEMP = $env:TMP

    if (-not $SkipCompile) {
        & $Python -m compileall backend src tests -q
        $ExitCode = $LASTEXITCODE
    }

    if ($ExitCode -eq 0) {
        & $Python -m pytest "--basetemp=$RunTempDir" @args
        $ExitCode = $LASTEXITCODE
    }
}
finally {
    $env:TMP = $OriginalTmp
    $env:TEMP = $OriginalTemp

    $ResolvedTaskTempRoot = [IO.Path]::GetFullPath($TaskTempRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $ResolvedRunTempDir = [IO.Path]::GetFullPath($RunTempDir)
    $RequiredPrefix = $ResolvedTaskTempRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedRunTempDir.StartsWith($RequiredPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a temporary directory outside the repository task temp root: $ResolvedRunTempDir"
    }
    if (Test-Path -LiteralPath $ResolvedRunTempDir) {
        Remove-Item -LiteralPath $ResolvedRunTempDir -Recurse -Force
    }
}

exit $ExitCode
