@echo off
rem Ejecuta la version ORIGINAL congelada en original\ (sin cambios). Uso: rpa-original ayuda
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rpa.ps1" original %*
exit /b %ERRORLEVEL%
