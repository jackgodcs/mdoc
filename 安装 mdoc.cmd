@echo off
setlocal
title mdoc installation
echo mdoc 安装程序将校验当前 ZIP 内的固定文件并安装到当前用户目录。
echo 它不会修改永久 PowerShell 执行策略，也不会绕过企业安全策略。
echo.
pause
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-mdoc.ps1"
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" echo 安装未完成，退出代码：%EXIT_CODE%
pause
exit /b %EXIT_CODE%
