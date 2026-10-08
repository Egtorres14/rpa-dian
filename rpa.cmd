@echo off
rem Lanzador de comandos del RPA DIAN. Uso: rpa ayuda
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rpa.ps1" %*
exit /b %ERRORLEVEL%
