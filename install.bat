@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [2/4] Upgrading pip and packaging tools...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo Failed to upgrade pip. Check your network connection.
    pause
    exit /b 1
)

echo [3/4] Installing project dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo Failed to install dependencies. Check your network connection.
    pause
    exit /b 1
)

echo [4/4] Preloading Whisper model...
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DISABLE_XET=1
".venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='models', local_files_only=False)"
if errorlevel 1 (
    echo Failed to download Whisper model. Check your network connection.
    pause
    exit /b 1
)

echo.
echo Done. Run run.bat or select .venv\Scripts\python.exe as the VS Code interpreter.
pause
