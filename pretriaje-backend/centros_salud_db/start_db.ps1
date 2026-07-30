<#
    Levanta un PostgreSQL local en 127.0.0.1:5438 con la base `centros_salud`.

    El cluster vive en postgres\pgdata (dentro del proyecto), no toca ninguna
    instalación de PostgreSQL del sistema y no necesita permisos de admin.

      .\start_db.ps1              # arranca; carga los datos si la base está vacía
      .\start_db.ps1 -Recargar    # fuerza recrear el schema y recargar los datos
#>
[CmdletBinding()]
param(
    [switch] $Recargar
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'postgres\pg_comun.ps1')

# los NOTICE de "DROP TABLE IF EXISTS" van a stderr y en PowerShell 5.1 eso
# se convierte en error terminante: los bajamos a warning
$env:PGOPTIONS = '-c client_min_messages=warning'

$DbHost  = '127.0.0.1'
$Puerto  = 5438
$DbName  = 'centros_salud'
$DbUser  = 'postgres'

$BaseDir = $PSScriptRoot
$PgDir   = Join-Path $BaseDir 'postgres'
$PgData  = Join-Path $PgDir 'pgdata'
$LogFile = Join-Path $PgDir 'postgres.log'
$Schema  = Join-Path $PgDir 'schema.sql'
$Seed    = Join-Path $PgDir 'seed.sql'

# ---------------------------------------------------------------- binarios
$PgBin = Assert-PgBin
Set-PgEncoding

$initdb   = Join-Path $PgBin 'initdb.exe'
$pg_ctl   = Join-Path $PgBin 'pg_ctl.exe'
$psql     = Join-Path $PgBin 'psql.exe'
$createdb = Join-Path $PgBin 'createdb.exe'

Write-Host "PostgreSQL: $PgBin" -ForegroundColor DarkGray

# ------------------------------------------------------- crear el cluster
if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
    Write-Host "Creando cluster en $PgData ..." -ForegroundColor Cyan
    & $initdb -D $PgData -U $DbUser -E UTF8 --auth=trust | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "initdb falló (código $LASTEXITCODE)" }

    # el puerto y la interfaz quedan fijos en la config del cluster
    Add-Content -Path (Join-Path $PgData 'postgresql.conf') -Encoding utf8 -Value @"

# --- Hackaton: base de centros de salud ---
port = $Puerto
listen_addresses = '$DbHost'
"@
    Write-Host "Cluster creado." -ForegroundColor Green
}

# Desde acá se llaman ejecutables nativos: en PowerShell 5.1 cualquier línea
# que escriban en stderr se transforma en error terminante si el preference
# es 'Stop'. Chequeamos $LASTEXITCODE a mano en cada llamada.
$ErrorActionPreference = 'Continue'

# ------------------------------------------------------------- arrancar
& $pg_ctl -D $PgData status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "El servidor ya estaba corriendo." -ForegroundColor DarkGray
} else {
    Write-Host "Arrancando PostgreSQL en ${DbHost}:${Puerto} ..." -ForegroundColor Cyan
    & $pg_ctl -D $PgData -l $LogFile -w start
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No arrancó. Últimas líneas de $LogFile :" -ForegroundColor Red
        if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 }
        exit 1
    }
}

# -------------------------------------------------------------- la base
$existe = & $psql -h $DbHost -p $Puerto -U $DbUser -d postgres -tAc `
    "SELECT 1 FROM pg_database WHERE datname = '$DbName'"
if ($existe -ne '1') {
    Write-Host "Creando base $DbName ..." -ForegroundColor Cyan
    & $createdb -h $DbHost -p $Puerto -U $DbUser $DbName
    if ($LASTEXITCODE -ne 0) { throw "createdb falló" }
}

# --------------------------------------------------------- schema y datos
if (-not (Test-Path $Seed)) {
    Write-Host "Generando seed.sql ..." -ForegroundColor Cyan
    & python (Join-Path $PgDir 'generar_seed.py')
}

# to_regclass no falla si la tabla todavía no existe (un SELECT sí lo haría)
$hayTabla = & $psql -h $DbHost -p $Puerto -U $DbUser -d $DbName -tAc `
    "SELECT to_regclass('public.centros') IS NOT NULL"
$n = 0
if ($hayTabla -eq 't') {
    $n = & $psql -h $DbHost -p $Puerto -U $DbUser -d $DbName -tAc `
        "SELECT count(*) FROM centros"
}

if ($Recargar -or [int]$n -eq 0) {
    Write-Host "Cargando schema y datos ..." -ForegroundColor Cyan
    & $psql -h $DbHost -p $Puerto -U $DbUser -d $DbName -q -v ON_ERROR_STOP=1 -f $Schema
    if ($LASTEXITCODE -ne 0) { throw "falló la carga del schema" }
    & $psql -h $DbHost -p $Puerto -U $DbUser -d $DbName -q -v ON_ERROR_STOP=1 -f $Seed
    if ($LASTEXITCODE -ne 0) { throw "falló la carga de los datos" }
} else {
    Write-Host "Datos ya cargados ($n centros). Usá -Recargar para rehacerlos." -ForegroundColor DarkGray
}

# -------------------------------------------------------------- resumen
$resumen = & $psql -h $DbHost -p $Puerto -U $DbUser -d $DbName -tAc @"
SELECT (SELECT count(*) FROM centros) || ' centros, ' ||
       (SELECT count(*) FROM especialidades) || ' especialidades, ' ||
       (SELECT count(*) FROM centro_especialidad) || ' relaciones'
"@

Write-Host ''
Write-Host 'PostgreSQL escuchando.' -ForegroundColor Green
Write-Host "  Host/puerto : ${DbHost}:${Puerto}"
Write-Host "  Base        : $DbName"
Write-Host "  Usuario     : $DbUser (sin contraseña, auth=trust, sólo local)"
Write-Host "  Contenido   : $resumen"
Write-Host "  URL         : postgresql://${DbUser}@${DbHost}:${Puerto}/${DbName}"
Write-Host ''
Write-Host '  psql        : ' -NoNewline
Write-Host "& '$psql' -h $DbHost -p $Puerto -U $DbUser -d $DbName" -ForegroundColor Yellow
Write-Host '  parar       : ' -NoNewline
Write-Host '.\stop_db.ps1' -ForegroundColor Yellow
Write-Host ''
