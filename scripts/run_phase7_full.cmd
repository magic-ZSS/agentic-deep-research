@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_phase7_full.ps1" %*
set "phase7_exit_code=%ERRORLEVEL%"
endlocal & exit /b %phase7_exit_code%
