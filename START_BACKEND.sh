#!/bin/bash
# Simple script to start the GoPro backend manually
# This is a temporary workaround until Electron auto-start is fixed

echo "Starting GoPro Backend..."

# Check if backend is already running
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "Backend is already running on port 8000"
    exit 0
fi

# Start the bundled backend
"/Applications/GoPro Control.app/Contents/Resources/backend/dist/gopro-backend/gopro-backend" &

echo "Backend started! You can now use the GoPro Control app."
echo "Backend logs will appear below:"
echo "---"
