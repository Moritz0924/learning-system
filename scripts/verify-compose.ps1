param(
    [int]$StartupTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposeFile = Join-Path $Root "docker-compose.yml"
$ExpectedComposeFile = [IO.Path]::GetFullPath((Join-Path $Root "docker-compose.yml"))
$ResolvedComposeFile = [IO.Path]::GetFullPath($ComposeFile)
if ($ResolvedComposeFile -ne $ExpectedComposeFile -or -not (Test-Path -LiteralPath $ResolvedComposeFile)) {
    throw "Refusing to operate on an unexpected Compose project: $ResolvedComposeFile"
}

$ComposeBaseArguments = @("compose", "--project-directory", $Root, "--file", $ResolvedComposeFile)
$RequiredServices = @("postgres", "redis", "minio", "backend", "worker", "scheduler", "frontend")

function Invoke-Compose {
    param([string[]]$ComposeArguments)
    & docker @ComposeBaseArguments @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-ComposeCapture {
    param([string[]]$ComposeArguments)
    $Output = & docker @ComposeBaseArguments @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArguments -join ' ') failed with exit code $LASTEXITCODE"
    }
    return ($Output -join "`n")
}

function Wait-ForComposeServices {
    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        $RunningOutput = Invoke-ComposeCapture -ComposeArguments @("ps", "--services", "--filter", "status=running")
        $RunningServices = @($RunningOutput -split "`r?`n" | Where-Object { $_ })
        $MissingServices = @($RequiredServices | Where-Object { $_ -notin $RunningServices })
        if ($MissingServices.Count -eq 0) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Compose services did not become running within $StartupTimeoutSeconds seconds"
}

function Invoke-HttpProbe {
    param([string]$Url)
    try {
        $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
        return @{ StatusCode = [int]$Response.StatusCode; Content = [string]$Response.Content }
    }
    catch {
        $Response = $_.Exception.Response
        if ($null -eq $Response) {
            throw
        }
        $StatusCode = [int]$Response.StatusCode
        $Content = [string]$_.ErrorDetails.Message
        if (-not $Content -and $Response.PSObject.Methods.Name -contains "GetResponseStream") {
            $Reader = New-Object IO.StreamReader($Response.GetResponseStream())
            try {
                $Content = $Reader.ReadToEnd()
            }
            finally {
                $Reader.Dispose()
            }
        }
        elseif (-not $Content -and $null -ne $Response.Content) {
            $Content = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        }
        return @{ StatusCode = $StatusCode; Content = $Content }
    }
}

function Wait-ForHttpProbe {
    param(
        [string]$Url,
        [int[]]$AllowedStatusCodes,
        [string]$RequiredPatternFor503 = ""
    )
    $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $LastFailure = "no response received"
    while ((Get-Date) -lt $Deadline) {
        try {
            $Probe = Invoke-HttpProbe -Url $Url
            if ($Probe.StatusCode -in $AllowedStatusCodes) {
                if (
                    $Probe.StatusCode -ne 503 -or
                    -not $RequiredPatternFor503 -or
                    $Probe.Content -match $RequiredPatternFor503
                ) {
                    return $Probe
                }
                $LastFailure = "HTTP 503 did not contain $RequiredPatternFor503"
            }
            else {
                $LastFailure = "HTTP $($Probe.StatusCode)"
            }
        }
        catch {
            $LastFailure = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "HTTP probe did not become ready at $Url within $StartupTimeoutSeconds seconds: $LastFailure"
}

Set-Location $Root

try {
    & docker version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is not available"
    }

    Invoke-Compose -ComposeArguments @("config", "--quiet")
    Invoke-Compose -ComposeArguments @("down", "-v", "--remove-orphans")
    Invoke-Compose -ComposeArguments @("build", "--no-cache")
    Invoke-Compose -ComposeArguments @("up", "-d")
    Wait-ForComposeServices

    $OpenApiProbe = Wait-ForHttpProbe -Url "http://127.0.0.1:8000/openapi.json" -AllowedStatusCodes @(200)
    $ReadinessProbe = Wait-ForHttpProbe -Url "http://127.0.0.1:8000/api/health/ready" -AllowedStatusCodes @(200, 503) -RequiredPatternFor503 "not_ready"
    $FrontendProbe = Wait-ForHttpProbe -Url "http://127.0.0.1:3000/" -AllowedStatusCodes @(200)

    $HeadsOutput = Invoke-ComposeCapture -ComposeArguments @("exec", "-T", "backend", "alembic", "-c", "backend/alembic.ini", "heads")
    if (($HeadsOutput | Select-String -Pattern "\(head\)" -AllMatches).Matches.Count -ne 1) {
        throw "Expected exactly one Alembic head in the backend container"
    }
    $CurrentOutput = Invoke-ComposeCapture -ComposeArguments @("exec", "-T", "backend", "alembic", "-c", "backend/alembic.ini", "current")
    if ($CurrentOutput -notmatch "\(head\)") {
        throw "The Compose database is not at the Alembic head"
    }

    foreach ($Service in @("backend", "worker", "scheduler")) {
        $Uid = (Invoke-ComposeCapture -ComposeArguments @("exec", "-T", $Service, "id", "-u")).Trim()
        if ($Uid -eq "0") {
            throw "$Service is running as root"
        }
    }

    Invoke-Compose -ComposeArguments @("ps")
    Write-Host "Compose verification passed."
}
catch {
    [Console]::Error.WriteLine("Compose verification failed: $($_.Exception.Message)")
    & docker @ComposeBaseArguments ps
    & docker @ComposeBaseArguments logs --no-color --tail 200
    exit 1
}
