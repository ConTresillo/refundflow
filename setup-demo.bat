@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer must be available on PATH.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 exit /b 1
echo Setup complete. Run run-demo.bat to launch the local app.
pause
