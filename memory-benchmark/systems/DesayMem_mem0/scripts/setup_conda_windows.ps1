# Setup DesayMem_mem0 conda env + local PostgreSQL/pgvector on Windows (no Docker).
param(
    [string]$EnvName = "DesayMem_mem0",
    [int]$Port = 5433,
    [string]$DbUser = "desaymem",
    [string]$DbPassword = "change_me",
    [string]$DbName = "desaymem"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PgData = Join-Path $ProjectRoot ".pgdata"
$CondaBase = (& conda info --base).Trim()
$EnvPath = Join-Path $CondaBase "envs\$EnvName"
$PgBin = Join-Path $EnvPath "Library\bin"

if (-not (Test-Path $PgBin)) {
    throw "Conda env '$EnvName' not found or PostgreSQL not installed. Run: conda create -n $EnvName python=3.12 -y; conda install -n $EnvName -c conda-forge postgresql=16 pgvector -y"
}

$env:PATH = "$PgBin;$env:PATH"
$env:PGDATA = $PgData
$env:PGPORT = "$Port"

function Invoke-Pg([string[]]$Args) {
    & (Join-Path $PgBin "psql.exe") @Args
}

Write-Host "==> PostgreSQL data dir: $PgData (port $Port)"

if (-not (Test-Path $PgData)) {
    Write-Host "==> Initializing PostgreSQL cluster..."
    & (Join-Path $PgBin "initdb.exe") -U postgres -E UTF8 --locale=C -D $PgData | Out-Null
    $conf = Join-Path $PgData "postgresql.conf"
    Add-Content $conf "`nport = $Port"
    Add-Content $conf "`nlisten_addresses = 'localhost'"
}

$running = & (Join-Path $PgBin "pg_ctl.exe") -D $PgData status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Starting PostgreSQL..."
    & (Join-Path $PgBin "pg_ctl.exe") -D $PgData -l (Join-Path $PgData "postgres.log") start -w | Out-Null
} else {
    Write-Host "==> PostgreSQL already running"
}

Write-Host "==> Ensuring role/database..."
$createUser = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DbUser') THEN
    CREATE ROLE $DbUser WITH LOGIN PASSWORD '$DbPassword' SUPERUSER;
  END IF;
END
`$`$;
"@
Invoke-Pg -Args @("-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", $createUser) | Out-Null

$dbExists = Invoke-Pg -Args @("-U", "postgres", "-d", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname='$DbName'")
if (-not ($dbExists -match "1")) {
    Invoke-Pg -Args @("-U", "postgres", "-d", "postgres", "-c", "CREATE DATABASE $DbName OWNER $DbUser;") | Out-Null
}

$Dsn = "postgresql://${DbUser}:${DbPassword}@localhost:${Port}/${DbName}"
Write-Host "==> Applying SQL migrations..."
Push-Location $ProjectRoot
conda run -n $EnvName python -m desaymem.cli apply --dsn $Dsn
Pop-Location

Write-Host ""
Write-Host "Setup complete."
Write-Host "  conda activate $EnvName"
Write-Host "  POSTGRES_DSN=$Dsn"
Write-Host "  Start PG : `$env:PGDATA='$PgData'; `$env:PATH='$PgBin;' + `$env:PATH; pg_ctl -D `$env:PGDATA start"
Write-Host "  Stop PG  : pg_ctl -D `$env:PGDATA stop"
Write-Host "  Run tests: `$env:DESAYMEM_ITEST_DSN='$Dsn'; python -m pytest -s -v"
