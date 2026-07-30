<#
    Consulta la base PostgreSQL del proyecto (127.0.0.1:5438) desde PowerShell,
    sin tener que acordarse de la ruta de psql ni pelear con las tildes.

      .\consultar.ps1                                     # sesión interactiva de psql
      .\consultar.ps1 "SELECT * FROM centros LIMIT 5;"
      .\consultar.ps1 "SELECT centro FROM vw_centros_especialidades
                       WHERE ciudad = 'Paraná';"          # los acentos funcionan
      .\consultar.ps1 -Archivo postgres\consultas.sql
      .\consultar.ps1 "SELECT * FROM centros;" -Csv | ConvertFrom-Csv
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromPipeline = $true)]
    [string] $Sql,

    [string] $Archivo,

    # salida CSV, para encadenar con ConvertFrom-Csv / Export-Excel
    [switch] $Csv
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'postgres\pg_comun.ps1')

$DbHost = '127.0.0.1'
$Puerto = 5438
$DbName = 'centros_salud'
$DbUser = 'postgres'

$psql = Join-Path (Assert-PgBin) 'psql.exe'
Set-PgEncoding

# ON_ERROR_STOP para que un SQL con error devuelva exit code != 0;
# sin esto psql termina en 0 aunque la consulta haya fallado
$comunes = @('-h', $DbHost, '-p', $Puerto, '-U', $DbUser, '-d', $DbName,
             '-v', 'ON_ERROR_STOP=1')
if ($Csv) { $comunes += '--csv' }

$ErrorActionPreference = 'Continue'

if ($Archivo) {
    if (-not (Test-Path $Archivo)) { throw "no existe el archivo: $Archivo" }
    & $psql @comunes -f $Archivo
}
elseif ($Sql) {
    # por stdin y no con -c: PowerShell 5.1 manda argv en ANSI y el servidor
    # rechaza los acentos con "invalid byte sequence for encoding UTF8"
    $Sql | & $psql @comunes
}
else {
    Write-Host "psql en ${DbHost}:${Puerto}/${DbName} — salí con \q" -ForegroundColor DarkGray
    & $psql @comunes
}

exit $LASTEXITCODE
