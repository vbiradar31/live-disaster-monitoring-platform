[CmdletBinding()]
param([switch]$Child)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not $Child) {
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Child
    $childExitCode = $LASTEXITCODE
    if ($null -eq $childExitCode) { $childExitCode = 1 }
    if ($childExitCode -ne 0) {
        throw "PostgreSQL integration workflow failed with exit code $childExitCode."
    }
    return
}

$composeFile = Join-Path $repoRoot 'docker-compose.integration.yml'
$projectName = 'ldmp-postgres-integration'
$composeAttempted = $false
$composeStarted = $false
$reuseService = $false
$workflowExitCode = 0
Push-Location $repoRoot
try {
    # Test-only credentials match the isolated service definition. They never
    # enter the caller's PowerShell process or the developer .env file.
    $testUrl = 'postgresql+psycopg://ldmp_test:ldmp_test_only@127.0.0.1:55432/postgres'
    $env:POSTGRES_TEST_ADMIN_URL = $testUrl
    $env:DATABASE_URL = $testUrl
    $env:LDMP_DISABLE_DOTENV = '1'

    $python = Join-Path $repoRoot 'ingestion\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'The repository virtual environment was not found at ingestion\.venv.'
    }

    $composeAttempted = $true
    & docker compose -f $composeFile -p $projectName up -d --wait
    if ($LASTEXITCODE -eq 0) {
        $composeStarted = $true
    }
    else {
        # Docker API access may be unavailable while the already-running,
        # isolated test service remains reachable. Reuse only after authenticating
        # against its guarded loopback test identity; never stop a service we did
        # not start in this process.
        & $python (Join-Path $repoRoot 'scripts\dev_database.py') --integration-preflight
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not start or verify the disposable PostgreSQL integration service.'
        }
        $reuseService = $true
    }

    & $python -m pytest -q tests/postgres_integration -rs
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL integration tests failed.' }
}
catch {
    [Console]::Error.WriteLine('PostgreSQL integration workflow failed.')
    $workflowExitCode = 1
}
finally {
    if ($composeAttempted -and -not $reuseService) {
        & docker compose -f $composeFile -p $projectName down --remove-orphans
        if ($LASTEXITCODE -ne 0 -and $composeStarted) {
            [Console]::Error.WriteLine('Could not clean up the disposable PostgreSQL integration service.')
            $workflowExitCode = 1
        }
    }
    Pop-Location
}

$host.SetShouldExit([int]$workflowExitCode)
