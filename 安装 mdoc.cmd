@echo off
setlocal
chcp 65001 > nul
title mdoc installation
echo The mdoc installer will verify the fixed files in this package and install them for the current user.
echo It does not change the permanent PowerShell execution policy or bypass enterprise security policy.
echo.
set NETWORK_ARG=
choice /C YN /N /M "Allow the installer to download and verify about 50 MB of mdoc Toolchain? [Y/N] "
if errorlevel 2 goto offline
set NETWORK_ARG=-AllowNetworkDownload
:offline
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-mdoc.ps1" %NETWORK_ARG%
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" echo Installation did not complete. Exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
