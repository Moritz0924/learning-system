param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PlaywrightArgs
)

$ErrorActionPreference = "Stop"
& node (Join-Path $PSScriptRoot "e2e-run.mjs") @PlaywrightArgs
exit $LASTEXITCODE
