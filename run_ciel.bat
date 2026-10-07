@echo off
setlocal
title CIEL 3.1 - Voice Assistant

cd /d "%~dp0"

echo.
echo ========================================
echo          CIEL 3.1
echo     Voice Assistant - Starting...
echo ========================================
echo.

echo [1/2] Checking Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python was not found.
    echo Please make sure Python 3.14 is installed and added to PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Installing/checking requirements...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Some requirements could not be installed.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo        Starting CIEL...
echo ========================================
echo.

python ciel.py

echo.
echo ========================================
echo        CIEL has stopped.
echo ========================================
pause