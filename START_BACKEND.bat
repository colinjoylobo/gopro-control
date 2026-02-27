@echo off
REM Simple script to start the GoPro backend manually on Windows
REM This is a temporary workaround until Electron auto-start is fixed

echo Starting GoPro Backend...

REM Check if backend is already running on port 8000
netstat -ano | findstr :8000 | findstr LISTENING >nul 2>&1
if %errorlevel%==0 (
    echo Backend is already running on port 8000
    exit /b 0
)

REM Try bundled executable first (installed via setup.ps1)
set BUNDLED_EXE=%LOCALAPPDATA%\Programs\GoPro Control\resources\backend\dist\gopro-backend\gopro-backend.exe
if exist "%BUNDLED_EXE%" (
    echo Starting bundled backend...
    start /B "" "%BUNDLED_EXE%"
    goto :started
)

REM Try development venv
set SCRIPT_DIR=%~dp0
set VENV_PYTHON=%SCRIPT_DIR%backend\venv\Scripts\python.exe
if exist "%VENV_PYTHON%" (
    echo Starting backend from venv...
    cd /d "%SCRIPT_DIR%backend"
    start /B "" "%VENV_PYTHON%" main.py
    goto :started
)

REM Fallback to system python
echo Starting backend with system python...
cd /d "%SCRIPT_DIR%backend"
start /B "" python main.py

:started
echo Backend started! You can now use the GoPro Control app.
echo Backend logs will appear in: %SCRIPT_DIR%backend\gopro_backend.log
echo ---
