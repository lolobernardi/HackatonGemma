$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --------------------------------------------------------------------------- #
# Entorno virtual
# --------------------------------------------------------------------------- #
# Un .venv es específico de la computadora que lo creó: pyvenv.cfg y los .exe de
# Scripts/ guardan la ruta absoluta del intérprete. Si el proyecto se copia entre
# máquinas (zip, OneDrive, pendrive), el .venv que viaja adentro queda muerto.
# Por eso acá se valida que el intérprete del venv realmente ejecute, y se
# recrea si no.

function Test-VenvUsable {
    $exe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $exe)) { return $false }
    & $exe -c "import sys" 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

if ((Test-Path ".\.venv") -and -not (Test-VenvUsable)) {
    Write-Host "El .venv existente apunta a un Python que no existe en esta computadora." -ForegroundColor Yellow
    Write-Host "Se recrea desde cero." -ForegroundColor Yellow
    Remove-Item ".\.venv" -Recurse -Force
}

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creando entorno virtual..."
    python -m venv .venv
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "Se creó .env a partir de .env.example." -ForegroundColor Green
}

# El tag del modelo es lo otro que cambia de máquina en máquina: /health lo
# compara literalmente contra lo que devuelve Ollama.
$modeloConfigurado = (Select-String -Path ".\.env" -Pattern "^MODELO=(.+)$").Matches.Groups[1].Value.Trim()
try {
    $tags = (Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
    $disponibles = $tags.models | ForEach-Object { $_.name }

    if ($disponibles -contains $modeloConfigurado) {
        Write-Host "Modelo '$modeloConfigurado' disponible en Ollama." -ForegroundColor Green
    } else {
        Write-Host "ATENCIÓN: .env pide MODELO=$modeloConfigurado pero Ollama tiene:" -ForegroundColor Red
        $disponibles | ForEach-Object { Write-Host "  - $_" }
        Write-Host "Ajustá MODELO en .env o corré: ollama pull $modeloConfigurado" -ForegroundColor Red
    }
} catch {
    Write-Host "ATENCIÓN: Ollama no responde en http://localhost:11434. Iniciá Ollama antes del backend." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Entorno preparado. Ahora ejecutá iniciar_backend.ps1 e iniciar_frontend.ps1 en terminales separadas."
