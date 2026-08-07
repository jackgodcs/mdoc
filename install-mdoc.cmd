@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-mdoc.ps1" %*
exit /b %errorlevel%
