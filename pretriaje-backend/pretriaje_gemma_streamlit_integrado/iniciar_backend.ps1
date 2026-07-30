$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Se usa el python del venv por ruta directa, sin activarlo: no depende de la
# política de ejecución de PowerShell ni del PATH del usuario.
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No hay entorno virtual. Corré primero: .\preparar_entorno.ps1" -ForegroundColor Red
    exit 1
}

& $python -c "import fastapi, uvicorn" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "El .venv no es usable en esta computadora o le faltan dependencias." -ForegroundColor Red
    Write-Host "Corré: .\preparar_entorno.ps1" -ForegroundColor Red
    exit 1
}

# --reload-dir acotado a `app`: sin esto WatchFiles vigila TODO el proyecto,
# incluido .venv. Con el proyecto dentro de OneDrive, la sincronización toca
# archivos en .venv\Lib\site-packages sin parar, el servidor se reinicia en
# loop y termina cerrándose solo. Solo interesa recargar ante cambios propios.
& $python -m uvicorn app.main:app --reload --reload-dir app --port 8000
