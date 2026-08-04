@echo off
setlocal
python -B "%~dp0screenshot_assistant.py" --repository "%CD%" %*
if errorlevel 1 pause
