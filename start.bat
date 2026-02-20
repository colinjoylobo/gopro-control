@echo off
REM GoPro Desktop App Launcher Script for Windows

echo ================================================
echo   GoPro Control Center - Starting Application
echo ================================================

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed!
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

REM Check if Node.js is installed
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Node.js is not installed!
    echo Please install Node.js 16 or higher
    pause
    exit /b 1
)

echo.
echo Step 1: Starting FastAPI Backend...
echo -------------------------------------

cd backend

REM Check if virtual environment exists
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install dependencies if needed
if not exist venv\.installed (
    echo Installing Python dependencies...
    pip install -r requirements.txt
    echo. > venv\.installed
)

REM Start backend in background
echo Starting backend server on http://127.0.0.1:8000
start /B python -m uvicorn main:app --host 127.0.0.1 --port 8000

echo Waiting for backend to start...
timeout /t 5 /nobreak >nul

echo Backend is running!

REM Start frontend
echo.
echo Step 2: Starting Electron Frontend...
echo --------------------------------------

cd ..\frontend

REM Install dependencies if needed
if not exist node_modules (
    echo Installing npm dependencies...
    call npm install
)

REM Start Electron
echo Starting Electron app...
set ELECTRON_START_URL=http://localhost:3000
call npm run electron-dev

echo.
echo Application closed successfully!
echo ================================================
pause
