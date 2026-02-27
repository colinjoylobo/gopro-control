#!/bin/bash

#################################################
# GoPro Control Center - One-Step Setup
# Installs prerequisites, builds, and installs
# Usage: ./setup.sh
#################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${CYAN}${BOLD}================================================${NC}"
echo -e "${CYAN}${BOLD}  GoPro Control Center - Setup & Install${NC}"
echo -e "${CYAN}${BOLD}================================================${NC}"
echo ""

OS="$(uname -s)"
ARCH="$(uname -m)"
MISSING=()

########################################
# Step 1: Detect OS
########################################
echo -e "${BOLD}[1/6] Detecting platform...${NC}"
if [ "$OS" = "Darwin" ]; then
    echo -e "  ${GREEN}✓ macOS detected ($ARCH)${NC}"
    PKG_MGR=""
    if command -v brew &> /dev/null; then
        PKG_MGR="brew"
        echo -e "  ${GREEN}✓ Homebrew found${NC}"
    fi
elif [ "$OS" = "Linux" ]; then
    echo -e "  ${GREEN}✓ Linux detected ($ARCH)${NC}"
    if command -v apt-get &> /dev/null; then
        PKG_MGR="apt"
    elif command -v yum &> /dev/null; then
        PKG_MGR="yum"
    elif command -v pacman &> /dev/null; then
        PKG_MGR="pacman"
    fi
else
    echo -e "  ${RED}✗ Unsupported OS: $OS. Use setup.ps1 for Windows.${NC}"
    exit 1
fi
echo ""

########################################
# Step 2: Check & install prerequisites
########################################
echo -e "${BOLD}[2/6] Checking prerequisites...${NC}"

# --- Homebrew (macOS only) ---
if [ "$OS" = "Darwin" ] && [ -z "$PKG_MGR" ]; then
    echo -e "  ${YELLOW}⚠ Homebrew not found. Installing...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [ "$ARCH" = "arm64" ] && [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    PKG_MGR="brew"
    echo -e "  ${GREEN}✓ Homebrew installed${NC}"
fi

# --- Python 3 ---
if command -v python3 &> /dev/null; then
    PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 8 ]; then
        echo -e "  ${GREEN}✓ Python $PY_VER${NC}"
    else
        echo -e "  ${YELLOW}⚠ Python $PY_VER found but 3.8+ required${NC}"
        MISSING+=("python3")
    fi
else
    echo -e "  ${YELLOW}⚠ Python 3 not found${NC}"
    MISSING+=("python3")
fi

# --- Node.js ---
if command -v node &> /dev/null; then
    NODE_VER=$(node --version | tr -d 'v')
    NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 16 ]; then
        echo -e "  ${GREEN}✓ Node.js v$NODE_VER${NC}"
    else
        echo -e "  ${YELLOW}⚠ Node.js v$NODE_VER found but 16+ required${NC}"
        MISSING+=("node")
    fi
else
    echo -e "  ${YELLOW}⚠ Node.js not found${NC}"
    MISSING+=("node")
fi

# --- npm ---
if command -v npm &> /dev/null; then
    echo -e "  ${GREEN}✓ npm $(npm --version)${NC}"
else
    echo -e "  ${YELLOW}⚠ npm not found${NC}"
    MISSING+=("npm")
fi

# --- ffmpeg ---
if command -v ffmpeg &> /dev/null; then
    echo -e "  ${GREEN}✓ ffmpeg found${NC}"
else
    echo -e "  ${YELLOW}⚠ ffmpeg not found (needed for live preview)${NC}"
    MISSING+=("ffmpeg")
fi

# --- git ---
if command -v git &> /dev/null; then
    echo -e "  ${GREEN}✓ git $(git --version | awk '{print $3}')${NC}"
else
    echo -e "  ${YELLOW}⚠ git not found${NC}"
    MISSING+=("git")
fi

# --- Install missing ---
if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo -e "  ${CYAN}Installing missing: ${MISSING[*]}${NC}"

    if [ "$OS" = "Darwin" ]; then
        for pkg in "${MISSING[@]}"; do
            case "$pkg" in
                python3) brew install python@3.12 ;;
                node|npm) brew install node ;;
                ffmpeg) brew install ffmpeg ;;
                git) brew install git ;;
            esac
        done
    elif [ "$PKG_MGR" = "apt" ]; then
        sudo apt-get update -qq
        for pkg in "${MISSING[@]}"; do
            case "$pkg" in
                python3) sudo apt-get install -y python3 python3-venv python3-pip ;;
                node|npm) curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs ;;
                ffmpeg) sudo apt-get install -y ffmpeg ;;
                git) sudo apt-get install -y git ;;
            esac
        done
    elif [ "$PKG_MGR" = "yum" ]; then
        for pkg in "${MISSING[@]}"; do
            case "$pkg" in
                python3) sudo yum install -y python3 ;;
                node|npm) curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - && sudo yum install -y nodejs ;;
                ffmpeg) sudo yum install -y ffmpeg ;;
                git) sudo yum install -y git ;;
            esac
        done
    elif [ "$PKG_MGR" = "pacman" ]; then
        for pkg in "${MISSING[@]}"; do
            case "$pkg" in
                python3) sudo pacman -S --noconfirm python ;;
                node|npm) sudo pacman -S --noconfirm nodejs npm ;;
                ffmpeg) sudo pacman -S --noconfirm ffmpeg ;;
                git) sudo pacman -S --noconfirm git ;;
            esac
        done
    else
        echo -e "  ${RED}✗ No supported package manager found. Please install manually: ${MISSING[*]}${NC}"
        exit 1
    fi

    echo -e "  ${GREEN}✓ All prerequisites installed${NC}"
fi

# Verify everything is now available
echo ""
echo -e "  ${BOLD}Verified:${NC}"
echo -e "  Python:  $(python3 --version 2>&1)"
echo -e "  Node.js: $(node --version 2>&1)"
echo -e "  npm:     $(npm --version 2>&1)"
echo -e "  ffmpeg:  $(ffmpeg -version 2>&1 | head -1)"
echo -e "  git:     $(git --version 2>&1)"
echo ""

########################################
# Step 3: Setup Python backend
########################################
echo -e "${BOLD}[3/6] Setting up Python backend...${NC}"
cd "$SCRIPT_DIR/backend"

if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate

echo "  Installing backend dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet pyinstaller

echo "  Building backend executable..."
pyinstaller gopro-backend.spec --clean --noconfirm 2>&1 | tail -3

if [ -f "dist/gopro-backend/gopro-backend" ]; then
    echo -e "  ${GREEN}✓ Backend built ($(du -h dist/gopro-backend/gopro-backend | cut -f1))${NC}"
else
    echo -e "  ${RED}✗ Backend build failed${NC}"
    deactivate
    exit 1
fi
deactivate
echo ""

########################################
# Step 4: Build React frontend
########################################
echo -e "${BOLD}[4/6] Building React frontend...${NC}"
cd "$SCRIPT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    echo "  Installing npm packages..."
    npm install --silent 2>&1 | tail -3
else
    echo -e "  ${GREEN}✓ node_modules exists${NC}"
fi

echo "  Building production bundle..."
npx react-scripts build 2>&1 | tail -5
echo -e "  ${GREEN}✓ Frontend built${NC}"
echo ""

########################################
# Step 5: Package Electron app
########################################
echo -e "${BOLD}[5/6] Packaging Electron app...${NC}"
npm run package 2>&1 | tail -5
echo -e "  ${GREEN}✓ Electron app packaged${NC}"
echo ""

########################################
# Step 6: Install to Applications
########################################
echo -e "${BOLD}[6/6] Installing...${NC}"

if [ "$OS" = "Darwin" ]; then
    APP_SRC=""
    if [ -d "dist/mac-arm64/GoPro Control.app" ]; then
        APP_SRC="dist/mac-arm64/GoPro Control.app"
    elif [ -d "dist/mac/GoPro Control.app" ]; then
        APP_SRC="dist/mac/GoPro Control.app"
    fi

    if [ -n "$APP_SRC" ]; then
        # Remove old version if exists
        if [ -d "/Applications/GoPro Control.app" ]; then
            echo "  Removing previous version..."
            rm -rf "/Applications/GoPro Control.app"
        fi
        echo "  Copying to /Applications..."
        cp -r "$APP_SRC" "/Applications/GoPro Control.app"
        echo -e "  ${GREEN}✓ Installed to /Applications/GoPro Control.app${NC}"
    else
        echo -e "  ${YELLOW}⚠ .app bundle not found in dist/. Check build output.${NC}"
        ls -la dist/ 2>/dev/null
    fi
elif [ "$OS" = "Linux" ]; then
    APPIMAGE=$(find dist/ -name "*.AppImage" 2>/dev/null | head -1)
    if [ -n "$APPIMAGE" ]; then
        chmod +x "$APPIMAGE"
        mkdir -p "$HOME/.local/bin"
        cp "$APPIMAGE" "$HOME/.local/bin/gopro-control"
        echo -e "  ${GREEN}✓ Installed to ~/.local/bin/gopro-control${NC}"
    fi
fi

echo ""
echo -e "${CYAN}${BOLD}================================================${NC}"
echo -e "${GREEN}${BOLD}  ✅ SETUP COMPLETE!${NC}"
echo -e "${CYAN}${BOLD}================================================${NC}"
echo ""
if [ "$OS" = "Darwin" ]; then
    echo -e "  Launch:  ${BOLD}open /Applications/GoPro\\ Control.app${NC}"
else
    echo -e "  Launch:  ${BOLD}gopro-control${NC}"
fi
echo -e "  Downloads saved to: ~/Documents/GoPro Downloads/"
echo ""
