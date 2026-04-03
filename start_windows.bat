@echo off
setlocal

echo ==========================================
echo YouthPlayer start (Windows)
echo ==========================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo [INFO] Please run setup_env_windows.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

python --version

REM Start app only
echo.
echo [INFO] Starting YouthPlayer...
echo [INFO] Web UI: http://127.0.0.1:5000
echo.
python .\main.py

echo.
echo [INFO] Process exited.
pause
exit /b 0
