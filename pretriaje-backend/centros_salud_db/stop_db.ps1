<#
    Para el PostgreSQL local del proyecto (el cluster de postgres\pgdata).
    No afecta ninguna otra instalación de PostgreSQL de la máquina.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'postgres\pg_comun.ps1')

$PgData = Join-Path (Join-Path $PSScriptRoot 'postgres') 'pgdata'

$PgBin = Assert-PgBin
if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
    Write-Host "No hay cluster en $PgData." -ForegroundColor DarkGray
    exit 0
}

$pg_ctl = Join-Path $PgBin 'pg_ctl.exe'
& $pg_ctl -D $PgData status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'El servidor no estaba corriendo.' -ForegroundColor DarkGray
    exit 0
}

& $pg_ctl -D $PgData -m fast -w stop
if ($LASTEXITCODE -eq 0) {
    Write-Host 'PostgreSQL detenido.' -ForegroundColor Green
} else {
    exit 1
}
