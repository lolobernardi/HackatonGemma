$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No hay entorno virtual. Corré primero: .\preparar_entorno.ps1" -ForegroundColor Red
    exit 1
}

& $python -c "import streamlit" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "El .venv no es usable en esta computadora o le falta streamlit." -ForegroundColor Red
    Write-Host "Corré: .\preparar_entorno.ps1" -ForegroundColor Red
    exit 1
}

$env:BACKEND_URL = "http://127.0.0.1:8000"

Write-Host ""
Write-Host "Abrí http://localhost:8501 en el navegador." -ForegroundColor Cyan
Write-Host ""

# `headless` y el resto de los ajustes de arranque viven en .streamlit/config.toml.
& $python -m streamlit run frontend/streamlit_app.py
