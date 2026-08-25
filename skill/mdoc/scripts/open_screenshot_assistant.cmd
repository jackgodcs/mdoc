@echo off
setlocal
python -B "%~dp0screenshot_assistant.py" --workspace "%CD%" %*
if errorlevel 1 pause
