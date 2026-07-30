<#
    Funciones compartidas por start_db.ps1, stop_db.ps1 y consultar.ps1.
    Se usa con dot-sourcing:  . "$PSScriptRoot\postgres\pg_comun.ps1"
#>

$script:PgEnvName = 'pg-hackaton'

function Get-PgBin {
    <# Devuelve la carpeta con los binarios de PostgreSQL, o $null. #>
    $cmd = Get-Command pg_ctl -ErrorAction SilentlyContinue
    if ($cmd) { return (Split-Path $cmd.Source) }

    $condaBase = $null
    if (Get-Command conda -ErrorAction SilentlyContinue) {
        $condaBase = (& conda info --base 2>$null | Select-Object -First 1)
    }
    foreach ($root in @($condaBase, "$env:USERPROFILE\anaconda3",
                        "$env:USERPROFILE\miniconda3")) {
        if (-not $root) { continue }
        foreach ($sub in @('Library\bin', 'bin')) {
            $p = Join-Path $root "envs\$script:PgEnvName\$sub"
            if (Test-Path (Join-Path $p 'pg_ctl.exe')) { return $p }
        }
    }
    return $null
}

function Assert-PgBin {
    <# Igual que Get-PgBin pero corta con un mensaje útil si no está. #>
    $bin = Get-PgBin
    if ($bin) { return $bin }
    Write-Host ''
    Write-Host 'No encontré los binarios de PostgreSQL.' -ForegroundColor Red
    Write-Host 'Instalalos en un entorno conda aislado (no necesita admin):'
    Write-Host ''
    Write-Host "  conda create -y -n $script:PgEnvName -c conda-forge postgresql" -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

function Set-PgEncoding {
    <#
        Deja la consola y el cliente en UTF-8.

        Importa porque los nombres de los centros llevan tildes. Con esto
        puesto, mandar SQL por stdin (pipe) funciona bien con acentos; pasarlo
        como argumento -c "..." NO, porque PowerShell 5.1 codifica argv en ANSI.
    #>
    $env:PGCLIENTENCODING = 'UTF8'
    try {
        chcp 65001 *> $null
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $global:OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    } catch {
        Write-Verbose "no pude fijar la consola en UTF-8: $_"
    }
}
