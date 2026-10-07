@echo off
setlocal
title CIEL Web Companion

cd /d "%~dp0"

python ciel_web.py

echo.
echo CIEL Web has stopped.
pause