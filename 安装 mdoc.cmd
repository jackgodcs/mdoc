@echo off
setlocal
chcp 65001 > nul
title mdoc installation
echo The mdoc installer will verify the fixed files in this package and install them for the current user.
echo It does not change the permanent PowerShell execution policy or bypass enterprise security policy.
echo.
set NETWORK_ARG=
set TOOLKIT_ARG=
if exist "%~dp0mdoc-toolchain.zip" goto local_toolkit
if exist "%~dp0mdoc-toolchain-2026.08.1-windows-x64.zip" goto local_toolkit
:select_source
choice /C LDN /N /M "Toolchain source: [L] enter local ZIP path, [D] download automatically, [N] exit "
if errorlevel 3 goto cancelled
if errorlevel 2 goto network
set TOOLKIT_PATH=
set /p "TOOLKIT_PATH=Enter the full path to the downloaded mdoc Toolchain ZIP:"
if not exist "%TOOLKIT_PATH%" (
  echo File not found: "%TOOLKIT_PATH%"
  echo.
  goto select_source
)
set TOOLKIT_ARG=-Toolkit "%TOOLKIT_PATH%"
goto install

:network
choice /C YN /N /M "Allow the installer to download and verify about 50 MB of mdoc Toolchain? [Y/N] "
if errorlevel 2 goto cancelled
set NETWORK_ARG=-AllowNetworkDownload
echo.

:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-mdoc.ps1" %TOOLKIT_ARG% %NETWORK_ARG%
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" (
  echo Installation did not complete. Exit code: %EXIT_CODE%
  echo.
  echo You can install without network access:
  echo 1. Download the Toolchain ZIP from:
  echo    https://github.com/jackgodcs/mdoc-toolchain/releases/download/v2026.08.1/mdoc-toolchain-2026.08.1-windows-x64.zip
  echo 2. Put it beside this installer, or keep it anywhere and run:
  echo    install-mdoc.cmd -Toolkit "%USERPROFILE%\Downloads\mdoc-toolchain-2026.08.1-windows-x64.zip"
  echo 3. Run that command from this package directory again.
)
pause
exit /b %EXIT_CODE%

:local_toolkit
echo A local mdoc Toolchain bundle was found. It will be verified and used without a network download.
echo.
goto install

:cancelled
echo Installation cancelled. No files were changed.
pause
exit /b 2
