#!/bin/bash

#################################################
# GoPro Control Center - Build Script
# Creates distributable desktop application
#################################################

set -e  # Exit on error

echo "================================================"
echo "  GoPro Control Center - Build Script"
echo "================================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Check prerequisites
echo "Step 1: Checking prerequisites..."
echo "-------------------------------------"

if ! command -v node &> /dev/null; then
    echo -e "${RED}❌ Node.js not found. Please install Node.js first.${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 not found. Please install Python 3 first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Node.js found: $(node --version)${NC}"
echo -e "${GREEN}✓ Python found: $(python3 --version)${NC}"
echo ""

# Step 2: Bundle Python backend with PyInstaller
echo "Step 2: Bundling Python backend..."
echo "-------------------------------------"

# Get the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install dependencies
source venv/bin/activate

# Install PyInstaller if not already installed
if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

# Check if requirements are installed
if ! python -c "import fastapi" 2>/dev/null; then
    echo "Installing backend dependencies..."
    pip install -r requirements.txt
fi

# Build backend with PyInstaller
echo "Building backend executable with PyInstaller..."
pyinstaller gopro-backend.spec --clean --noconfirm

if [ -f "dist/gopro-backend/gopro-backend" ]; then
    echo -e "${GREEN}✓ Backend bundled successfully${NC}"
    echo "  Size: $(du -h dist/gopro-backend/gopro-backend | cut -f1)"
else
    echo -e "${RED}❌ Backend build failed${NC}"
    exit 1
fi

deactivate
cd "$SCRIPT_DIR/frontend"
echo ""

# Step 3: Install frontend dependencies
echo "Step 3: Installing frontend dependencies..."
echo "-------------------------------------"

if [ ! -d "node_modules" ]; then
    echo "Installing npm packages..."
    npm install
else
    echo -e "${GREEN}✓ node_modules already exists${NC}"
fi
echo ""

# Step 4: Build React app
echo "Step 4: Building React app..."
echo "-------------------------------------"
npm run build
echo -e "${GREEN}✓ React app built successfully${NC}"
echo ""

# Step 5: Package Electron app
echo "Step 5: Packaging Electron app..."
echo "-------------------------------------"
npm run package

echo ""
echo "================================================"
echo -e "${GREEN}✅ BUILD COMPLETE!${NC}"
echo "================================================"
echo ""
echo "Output location:"
if [ "$(uname)" = "Darwin" ]; then
    echo "  macOS: frontend/dist/GoPro Control-1.0.0-arm64.dmg"
elif [ "$(uname)" = "Linux" ]; then
    echo "  Linux: frontend/dist/GoPro Control-1.0.0.AppImage"
fi
echo ""
echo -e "${GREEN}✨ This is a STANDALONE build!${NC}"
echo "   Python is bundled inside - users don't need to install anything!"
echo ""
echo "To test the app:"
if [ "$(uname)" = "Darwin" ]; then
    echo "  open frontend/dist/mac-arm64/GoPro\ Control.app"
elif [ "$(uname)" = "Linux" ]; then
    echo "  ./frontend/dist/GoPro\ Control-1.0.0.AppImage"
fi
echo ""
echo "Files will be saved to:"
echo "  ~/Documents/GoPro Downloads/YYYY-MM-DD_GoProXXXX/"
echo ""
