#################################################
# GoPro Control Center - One-Step Setup (Windows)
# Installs prerequisites, builds, and installs
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1
#################################################

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  GoPro Control Center - Setup & Install" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

$Missing = @()

########################################
# Step 1: Detect platform
########################################
Write-Host "[1/6] Detecting platform..." -ForegroundColor White
Write-Host "  OK Windows $([Environment]::OSVersion.Version)" -ForegroundColor Green

# Check for winget or choco
$PkgMgr = $null
if (Get-Command winget -ErrorAction SilentlyContinue) {
    $PkgMgr = "winget"
    Write-Host "  OK winget found" -ForegroundColor Green
} elseif (Get-Command choco -ErrorAction SilentlyContinue) {
    $PkgMgr = "choco"
    Write-Host "  OK chocolatey found" -ForegroundColor Green
}
Write-Host ""

########################################
# Step 2: Check & install prerequisites
########################################
Write-Host "[2/6] Checking prerequisites..." -ForegroundColor White

# --- Python 3 ---
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $pyVer = & python --version 2>&1
    Write-Host "  OK $pyVer" -ForegroundColor Green
} else {
    $py3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($py3) {
        $pyVer = & python3 --version 2>&1
        Write-Host "  OK $pyVer" -ForegroundColor Green
    } else {
        Write-Host "  !! Python 3 not found" -ForegroundColor Yellow
        $Missing += "python"
    }
}

# --- Node.js ---
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVer = & node --version 2>&1
    Write-Host "  OK Node.js $nodeVer" -ForegroundColor Green
} else {
    Write-Host "  !! Node.js not found" -ForegroundColor Yellow
    $Missing += "node"
}

# --- npm ---
if (Get-Command npm -ErrorAction SilentlyContinue) {
    $npmVer = & npm --version 2>&1
    Write-Host "  OK npm $npmVer" -ForegroundColor Green
} else {
    Write-Host "  !! npm not found" -ForegroundColor Yellow
    $Missing += "npm"
}

# --- ffmpeg ---
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "  OK ffmpeg found" -ForegroundColor Green
} else {
    Write-Host "  !! ffmpeg not found (needed for live preview)" -ForegroundColor Yellow
    $Missing += "ffmpeg"
}

# --- git ---
if (Get-Command git -ErrorAction SilentlyContinue) {
    $gitVer = & git --version 2>&1
    Write-Host "  OK $gitVer" -ForegroundColor Green
} else {
    Write-Host "  !! git not found" -ForegroundColor Yellow
    $Missing += "git"
}

# --- Install missing ---
if ($Missing.Count -gt 0) {
    Write-Host ""
    Write-Host "  Installing missing: $($Missing -join ', ')" -ForegroundColor Cyan

    if (-not $PkgMgr) {
        Write-Host "  !! No package manager found. Installing winget packages..." -ForegroundColor Yellow
        Write-Host "  Please install manually if winget fails:" -ForegroundColor Yellow
        Write-Host "    Python: https://www.python.org/downloads/" -ForegroundColor White
        Write-Host "    Node.js: https://nodejs.org/" -ForegroundColor White
        Write-Host "    ffmpeg: https://ffmpeg.org/download.html" -ForegroundColor White
        Write-Host "    git: https://git-scm.com/downloads" -ForegroundColor White
        $PkgMgr = "winget"
    }

    foreach ($pkg in $Missing) {
        Write-Host "  Installing $pkg..." -ForegroundColor Cyan
        switch ($PkgMgr) {
            "winget" {
                switch ($pkg) {
                    "python" { winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
                    "node"   { winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements }
                    "npm"    { Write-Host "  npm comes with Node.js" -ForegroundColor Green }
                    "ffmpeg" { winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements }
                    "git"    { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
                }
            }
            "choco" {
                switch ($pkg) {
                    "python" { choco install python3 -y }
                    "node"   { choco install nodejs-lts -y }
                    "npm"    { Write-Host "  npm comes with Node.js" -ForegroundColor Green }
                    "ffmpeg" { choco install ffmpeg -y }
                    "git"    { choco install git -y }
                }
            }
        }
    }

    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Host "  OK Prerequisites installed (you may need to restart terminal if commands aren't found)" -ForegroundColor Green
}

Write-Host ""

########################################
# Step 3: Setup Python backend
########################################
Write-Host "[3/6] Setting up Python backend..." -ForegroundColor White
Set-Location "$ScriptDir\backend"

# Determine python command
$PythonCmd = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $PythonCmd = "python3"
}

if (-not (Test-Path "venv")) {
    Write-Host "  Creating virtual environment..."
    & $PythonCmd -m venv venv
}

# Activate venv
& ".\venv\Scripts\Activate.ps1"

Write-Host "  Installing backend dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet pyinstaller

Write-Host "  Building backend executable..."
pyinstaller gopro-backend.spec --clean --noconfirm 2>&1 | Select-Object -Last 3

if (Test-Path "dist\gopro-backend\gopro-backend.exe") {
    $size = (Get-Item "dist\gopro-backend\gopro-backend.exe").Length / 1MB
    Write-Host "  OK Backend built ($([math]::Round($size, 1)) MB)" -ForegroundColor Green
} else {
    Write-Host "  FAIL Backend build failed" -ForegroundColor Red
    deactivate
    exit 1
}

deactivate
Write-Host ""

########################################
# Step 4: Build React frontend
########################################
Write-Host "[4/6] Building React frontend..." -ForegroundColor White
Set-Location "$ScriptDir\frontend"

if (-not (Test-Path "node_modules")) {
    Write-Host "  Installing npm packages..."
    npm install --silent 2>&1 | Select-Object -Last 3
} else {
    Write-Host "  OK node_modules exists" -ForegroundColor Green
}

Write-Host "  Building production bundle..."
npx react-scripts build 2>&1 | Select-Object -Last 5
Write-Host "  OK Frontend built" -ForegroundColor Green
Write-Host ""

########################################
# Step 5: Package Electron app
########################################
Write-Host "[5/6] Packaging Electron app..." -ForegroundColor White
npm run package 2>&1 | Select-Object -Last 5
Write-Host "  OK Electron app packaged" -ForegroundColor Green
Write-Host ""

########################################
# Step 6: Install
########################################
Write-Host "[6/6] Installing..." -ForegroundColor White

$Installer = Get-ChildItem -Path "dist" -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Installer) {
    Write-Host "  Launching installer: $($Installer.Name)" -ForegroundColor Cyan
    Start-Process $Installer.FullName
    Write-Host "  OK Installer launched - follow the prompts" -ForegroundColor Green
} else {
    # Check for unpacked app
    $UnpackedDir = Get-ChildItem -Path "dist" -Directory -Filter "win-*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($UnpackedDir) {
        $DesktopShortcut = [System.IO.Path]::Combine([Environment]::GetFolderPath("Desktop"), "GoPro Control.lnk")
        $ExePath = Get-ChildItem -Path $UnpackedDir.FullName -Filter "GoPro Control.exe" -Recurse | Select-Object -First 1
        if ($ExePath) {
            $WshShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut($DesktopShortcut)
            $Shortcut.TargetPath = $ExePath.FullName
            $Shortcut.Save()
            Write-Host "  OK Desktop shortcut created" -ForegroundColor Green
        }
    } else {
        Write-Host "  !! No installer found in dist/. Check build output." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  SETUP COMPLETE!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Downloads saved to: ~\Documents\GoPro Downloads\" -ForegroundColor White
Write-Host ""
