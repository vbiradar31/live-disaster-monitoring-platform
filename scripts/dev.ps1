[CmdletBinding()]
param(
    [ValidateSet('Target', 'Migrate', 'Alembic', 'Api')]
    [string]$Operation = 'Target',
    [string[]]$AlembicArguments = @(),
    [switch]$Child,
    [string]$ArgumentPayload
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot 'ingestion\.venv\Scripts\python.exe'
$alembic = Join-Path $repoRoot 'ingestion\.venv\Scripts\alembic.exe'
$uvicorn = Join-Path $repoRoot 'ingestion\.venv\Scripts\uvicorn.exe'

if (-not $Child) {
    $payloadJson = ConvertTo-Json -InputObject @($AlembicArguments) -Compress
    $payload = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($payloadJson))
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath `
        -Child -Operation $Operation -ArgumentPayload $payload
    $childExitCode = $LASTEXITCODE
    if ($null -eq $childExitCode) { $childExitCode = 1 }
    if ($childExitCode -ne 0) {
        throw "Development operation '$Operation' failed with exit code $childExitCode."
    }
    return
}

Push-Location $repoRoot
try {
    # Host development commands always resolve DATABASE_URL from repository .env.
    Remove-Item Env:DATABASE_URL, Env:POSTGRES_TEST_ADMIN_URL, Env:LDMP_DISABLE_DOTENV `
        -ErrorAction SilentlyContinue
    $env:PYTHONPATH = Join-Path $repoRoot 'ingestion'

    if (-not (Test-Path -LiteralPath $python)) {
        throw 'The repository virtual environment was not found at ingestion\.venv.'
    }

    switch ($Operation) {
        'Target' {
            & $python (Join-Path $repoRoot 'scripts\dev_database.py') --target-only
            $operationExitCode = $LASTEXITCODE
        }
        { $_ -in 'Migrate', 'Alembic' } {
            & $python (Join-Path $repoRoot 'scripts\dev_database.py') --migration-preflight
            if ($LASTEXITCODE -ne 0) {
                $operationExitCode = 2
                break
            }
            if (-not (Test-Path -LiteralPath $alembic)) {
                throw 'Alembic executable was not found in the repository virtual environment.'
            }
            if ($Operation -eq 'Migrate') {
                $arguments = @('upgrade', 'head')
            }
            else {
                $decoded = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($ArgumentPayload)) |
                    ConvertFrom-Json
                $arguments = @($decoded | ForEach-Object { [string]$_ })
                if (-not $arguments -or $arguments.Count -eq 0) { $arguments = @('current') }
            }
            $savedErrorActionPreference = $ErrorActionPreference
            try {
                # Windows PowerShell represents native stderr as error records.
                # With Stop, Alembic diagnostics throw into the generic catch
                # before this block can redact and print their contents.
                $ErrorActionPreference = 'Continue'
                $alembicOutput = @(& $alembic @arguments 2>&1)
                $alembicExitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $savedErrorActionPreference
            }
            foreach ($line in $alembicOutput) {
                $safeLine = [regex]::Replace(
                    [string]$line,
                    '(?i)postgres(?:ql)?(?:\+\w+)?://[^\s''"<>]+',
                    '[database URL redacted]'
                )
                Write-Output $safeLine
            }
            $operationExitCode = $alembicExitCode
        }
        'Api' {
            if (-not (Test-Path -LiteralPath $uvicorn)) {
                throw 'Uvicorn executable was not found in the repository virtual environment.'
            }
            & $uvicorn app.main:app --host 127.0.0.1 --port 8000
            $operationExitCode = $LASTEXITCODE
        }
    }
}
catch {
    [Console]::Error.WriteLine('Development operation failed; see sanitized command output above.')
    $operationExitCode = 1
}
finally {
    Pop-Location
}

if ($null -eq $operationExitCode) { $operationExitCode = 1 }
$host.SetShouldExit([int]$operationExitCode)
