@echo off
REM Evita el error "no digitally signed" de la politica de ejecucion de PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0preparar_entorno.ps1"
pause
