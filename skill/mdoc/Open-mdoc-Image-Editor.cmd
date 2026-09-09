@echo off
setlocal
set "MDOC_PYTHON=%LOCALAPPDATA%\mdoc\runtime\Scripts\pythonw.exe"
if not exist "%MDOC_PYTHON%" set "MDOC_PYTHON=%LOCALAPPDATA%\mdoc\runtime\Scripts\python.exe"
set "MDOC_EDITOR=%~dp0scripts\standalone_image_editor.py"
if not exist "%MDOC_PYTHON%" (
  echo mdoc runtime is not installed for this user.
  pause
  exit /b 2
)
if not exist "%MDOC_EDITOR%" (
  echo mdoc standalone image editor is not installed for this user.
  pause
  exit /b 2
)
if "%~1"=="" (
  "%MDOC_PYTHON%" -B "%MDOC_EDITOR%"
) else (
  "%MDOC_PYTHON%" -B "%MDOC_EDITOR%" "%~1"
)
exit /b %ERRORLEVEL%
