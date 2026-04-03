@echo off
setlocal

echo ==========================================
echo YouthPlayer environment setup (Windows)
echo ==========================================

REM 1) Locate Python
set "PY_CMD="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=py -3"
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python not found. Please install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)

echo [INFO] Python command: %PY_CMD%

REM 2) Create venv if missing
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Virtual environment already exists.
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

python --version

REM 3) Install Python dependencies with CN mirrors
echo [INFO] Upgrading pip/setuptools/wheel from Tsinghua mirror...
python -m pip install -U pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo [WARN] Tsinghua mirror failed, trying Aliyun mirror...
    python -m pip install -U pip setuptools wheel -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
)

echo [INFO] Installing Python requirements...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo [WARN] Tsinghua mirror failed, trying Aliyun mirror...
    python -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
    if errorlevel 1 (
        echo [ERROR] Failed to install Python dependencies.
        pause
        exit /b 1
    )
)

REM 4) Install Node dependencies with CN mirror (optional)
where npm >nul 2>nul
if %errorlevel%==0 (
    echo [INFO] npm found. Setting registry to npmmirror and installing...
    call npm config set registry https://registry.npmmirror.com
    call npm install
    if errorlevel 1 (
        echo [WARN] npm install failed, retry after cache clean...
        call npm cache clean --force
        call npm install
    )
) else (
    echo [INFO] npm not found, skip Node install.
)

echo.
echo [INFO] Environment setup finished.
echo [INFO] Next step: run start_windows.bat
pause
exit /b 0
