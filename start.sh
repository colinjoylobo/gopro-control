#!/bin/bash

# GoPro Desktop App Launcher Script

echo "================================================"
echo "  GoPro Control Center - Starting Application"
echo "================================================"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed!"
    echo "Please install Python 3.8 or higher"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "Error: Node.js is not installed!"
    echo "Please install Node.js 16 or higher"
    exit 1
fi

# Function to check if port is in use
check_port() {
    if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null ; then
        echo "Warning: Port 8000 is already in use!"
        echo "Killing existing process..."
        lsof -ti:8000 | xargs kill -9
        sleep 2
    fi
}

# Start backend
echo ""
echo "Step 1: Starting FastAPI Backend..."
echo "-------------------------------------"

cd backend || exit

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies if needed
if [ ! -f "venv/.installed" ]; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
    touch venv/.installed
fi

# Check port
check_port

# Start backend in background
echo "Starting backend server on http://127.0.0.1:8000"
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Backend PID: $BACKEND_PID"

# Wait for backend to start
echo "Waiting for backend to start..."
sleep 5

# Check if backend is running
if ! curl -s http://127.0.0.1:8000/health > /dev/null; then
    echo "Error: Backend failed to start!"
    kill $BACKEND_PID 2>/dev/null
    exit 1
fi

echo "Backend is running!"

# Start frontend
echo ""
echo "Step 2: Starting Electron Frontend..."
echo "--------------------------------------"

cd ../frontend || exit

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Start Electron
echo "Starting Electron app..."
ELECTRON_START_URL=http://localhost:3000 npm run electron-dev

# Cleanup on exit
echo ""
echo "Shutting down..."
echo "Stopping backend (PID: $BACKEND_PID)..."
kill $BACKEND_PID 2>/dev/null

echo ""
echo "Application closed successfully!"
echo "================================================"
