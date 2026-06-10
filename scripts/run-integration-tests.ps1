<#
.SYNOPSIS
    Create a temporary test database, run integration tests, then drop it.

.DESCRIPTION
    Reads DB connection details from .env (same defaults as docker-compose).
    Uses the running Docker Compose postgres container to create/drop the test DB
    (no local psql required). The test URL connects from the host via localhost.

.PARAMETER DbPort
    PostgreSQL port exposed on the host. Default: reads DB_PORT from .env, fallback 5432.

.PARAMETER DbUser
    PostgreSQL user. Default: reads DB_USER from .env.

.PARAMETER DbPassword
    Password for DbUser. Default: reads DB_PASSWORD from .env.

.PARAMETER TestDbName
    Name of the temporary test database to create. Default: scheduler_test.

.PARAMETER PytestArgs
    Extra arguments forwarded to pytest (e.g. "-v -k test_something").

.EXAMPLE
    .\scripts\run-integration-tests.ps1

.EXAMPLE
    .\scripts\run-integration-tests.ps1 -PytestArgs "-v -k test_schedule"
#>
[CmdletBinding()]
param(
    [string]$DbPort,
    [string]$DbUser,
    [string]$DbPassword,
    [string]$TestDbName = "scheduler_test",
    [string]$PytestArgs = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Load .env ────────────────────────────────────────────────────────────────
$envFile = Join-Path (Join-Path $PSScriptRoot "..") ".env"
$envVars = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^\s*[^#]\S+=\S*" } | ForEach-Object {
        $parts = $_ -split "=", 2
        $envVars[$parts[0].Trim()] = $parts[1].Trim()
    }
}

function Resolve-EnvVar([string]$param, [string]$key, [string]$fallback) {
    if ($param) { return $param }
    if ($envVars.ContainsKey($key)) { return $envVars[$key] }
    return $fallback
}

$DbPort     = Resolve-EnvVar $DbPort     "DB_PORT"     "5432"
$DbUser     = Resolve-EnvVar $DbUser     "DB_USER"     "postgres"
$DbPassword = Resolve-EnvVar $DbPassword "DB_PASSWORD" ""

# Tests connect from the host via localhost; the container handles CREATE/DROP internally.
$testUrl = "postgresql+asyncpg://${DbUser}:${DbPassword}@localhost:${DbPort}/${TestDbName}"

# ── Pre-flight: postgres container reachable? ────────────────────────────────
$pgContainer = docker ps --filter "ancestor=postgres:16-alpine" --filter "status=running" --format "{{.Names}}" 2>&1
if (-not $pgContainer) {
    Write-Error "No running postgres:16-alpine container found. Start it with: docker compose up postgres -d"
    exit 1
}
Write-Host ">> Using container: $pgContainer"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Invoke-Psql([string]$sql) {
    # Use Continue so PostgreSQL NOTICE/WARNING lines on stderr don't abort the script.
    $local:ErrorActionPreference = "Continue"
    docker exec -i $pgContainer psql --username=$DbUser --tuples-only --command=$sql postgres
    if ($LASTEXITCODE -ne 0) {
        throw "psql exited with code $LASTEXITCODE for: $sql"
    }
}

# ── Create test DB ────────────────────────────────────────────────────────────
Write-Host ">> Creating test database '$TestDbName' (container: $pgContainer) ..."
Invoke-Psql "DROP DATABASE IF EXISTS `"$TestDbName`";" | Out-Null
Invoke-Psql "CREATE DATABASE `"$TestDbName`";" | Out-Null
Write-Host "   Created."

# ── Run tests ─────────────────────────────────────────────────────────────────
$exitCode = 0
try {
    Write-Host ""
    Write-Host ">> Running integration tests ..."
    Write-Host ""

    $env:TEST_DB_URL = $testUrl
    if ($PytestArgs) {
        uv run pytest tests/integration/ $PytestArgs.Split(" ")
    } else {
        uv run pytest tests/integration/ -v
    }
    $exitCode = $LASTEXITCODE
} finally {
    $env:TEST_DB_URL = ""

    # ── Drop test DB ──────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host ">> Dropping test database '$TestDbName' ..."
    # Terminate active connections first so DROP doesn't block
    Invoke-Psql @"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$TestDbName' AND pid <> pg_backend_pid();
"@ | Out-Null
    Invoke-Psql "DROP DATABASE IF EXISTS `"$TestDbName`";" | Out-Null
    Write-Host "   Dropped."
}

exit $exitCode
