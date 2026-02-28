@echo off
setlocal enabledelayedexpansion

REM #################################################
REM  GoPro Control Center - Build Script (Windows)
REM  Creates distributable desktop application
REM #################################################

echo ================================================
echo   GoPro Control Center - Build Script
echo ================================================
echo.

REM Step 1: Check prerequisites
echo Step 1: Checking prerequisites...
echo -------------------------------------

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Node.js not found. Please install Node.js first.
    exit /b 1
)

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.8+ first.
    exit /b 1
)

for /f "tokens=*" %%v in ('node --version') do echo OK Node.js %%v
for /f "tokens=*" %%v in ('python --version') do echo OK %%v
echo.

REM Step 2: Bundle Python backend with PyInstaller
echo Step 2: Bundling Python backend...
echo -------------------------------------

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%backend"

if not exist venv (
    echo Creating Python virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

REM Install PyInstaller if not already installed
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Install requirements if needed
python -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing backend dependencies...
    pip install -r requirements.txt
)

echo Building backend executable with PyInstaller...
pyinstaller gopro-backend.spec --clean --noconfirm

if exist "dist\gopro-backend\gopro-backend.exe" (
    echo OK Backend bundled successfully
) else (
    echo ERROR: Backend build failed
    call deactivate
    exit /b 1
)

call deactivate
cd /d "%SCRIPT_DIR%frontend"
echo.

REM Step 3: Install frontend dependencies
echo Step 3: Installing frontend dependencies...
echo -------------------------------------

if not exist node_modules (
    echo Installing npm packages...
    call npm install
) else (
    echo OK node_modules already exists
)
echo.

REM Step 4: Build React app
echo Step 4: Building React app...
echo -------------------------------------
call npm run build
echo OK React app built successfully
echo.

REM Step 5: Package Electron app
echo Step 5: Packaging Electron app...
echo -------------------------------------
call npm run package

echo.
echo ================================================
echo   BUILD COMPLETE!
echo ================================================
echo.
echo Output location:
echo   Windows: frontend\dist\GoPro Control Setup 1.0.0.exe
echo.
echo This is a STANDALONE build!
echo   Python is bundled inside - users don't need to install anything!
echo.
echo To test the app:
echo   Check frontend\dist\ for the installer or unpacked directory
echo.
echo Files will be saved to:
echo   %%USERPROFILE%%\Documents\GoPro Downloads\YYYY-MM-DD_GoProXXXX\
echo.
pause
