@echo off
REM ============================================================
REM  AmbieZ — One-click EXE builder for Windows
REM  Run this script in the same folder as AmbienZ.py
REM ============================================================

echo [1/4] Checking Python...
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python not found. Install from https://python.org
    pause & exit /b 1
)

echo [2/4] Installing dependencies...
pip install pyinstaller PySide6 opencv-python-headless numpy mss

echo [3/4] Building EXE (this may take 1-3 minutes)...
pyinstaller AmbienZ.spec --clean --noconfirm

echo [4/4] Done!
IF EXIST "dist\AmbienZ.exe" (
    echo.
    echo  SUCCESS: dist\AmbienZ.exe is ready!
    echo  Copy it anywhere and run — no Python needed.
) ELSE (
    echo  Something went wrong. Check the output above for errors.
)

pause
