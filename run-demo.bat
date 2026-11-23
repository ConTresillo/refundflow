@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo RefundFlow virtual environment is missing.
    echo Run setup-demo.bat once, then run this launcher again.
    pause
    exit /b 1
)
set "REFUNDFLOW_ENV=development"
echo Starting RefundFlow demo on http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.web:app --host 127.0.0.1 --port 8000 --no-proxy-headers
