@echo off
setlocal
chcp 65001 > nul
title mdoc Uninstaller
set "UNINSTALLER=%LOCALAPPDATA%\mdoc\uninstall\uninstall-mdoc.ps1"
if not exist "%UNINSTALLER%" set "UNINSTALLER=%~dp0uninstall-mdoc.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%UNINSTALLER%" %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if "%EXIT_CODE%"=="0" echo mdoc was uninstalled successfully. Restart Codex to refresh the installed skills list.
if "%EXIT_CODE%"=="3" echo mdoc was uninstalled. Some locked files will be removed at the next sign-in.
if "%EXIT_CODE%"=="5" echo mdoc was partially uninstalled. Review the reported managed Python or registry leftovers.
if not "%EXIT_CODE%"=="0" if not "%EXIT_CODE%"=="3" if not "%EXIT_CODE%"=="5" echo mdoc uninstall did not complete. Exit code: %EXIT_CODE%
if not "%MDOC_UNINSTALL_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
