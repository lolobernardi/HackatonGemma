@echo off
REM ===========================================================================
REM  Arranque completo del pre-triaje, con doble clic.
REM
REM  Existe para saltear la politica de ejecucion de PowerShell, que en una
REM  maquina recien instalada rechaza los .ps1 con "is not digitally signed".
REM
REM  %~dp0 es la carpeta de ESTE archivo, asi que no hay ninguna ruta fija:
REM  el proyecto se puede copiar a cualquier lado y sigue funcionando.
REM ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar_todo.ps1" %*
pause
