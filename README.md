# GoPro Control Center - Desktop Application

A cross-platform desktop application for controlling multiple GoPro cameras simultaneously. Built with Electron, React, and FastAPI.

## Features

- **Multi-Camera Management**: Add, configure, and manage multiple GoPro cameras
- **Auto-Discovery**: Automatically discover GoPro cameras via Bluetooth
- **Synchronized Recording**: Start and stop recording on all cameras simultaneously
- **Bulk Download**: Download all videos from cameras in descending order (newest first)
- **S3 Upload**: Upload downloaded videos to S3 cloud storage
- **Real-time Progress**: WebSocket-based progress tracking for downloads
- **Cross-Platform**: Supports macOS, Windows, and Linux

## Architecture

### Backend (FastAPI + Python)
- BLE connection management via `open-gopro`
- Cross-platform WiFi management
- Media download and upload handling
- WebSocket for real-time updates

### Frontend (Electron + React)
- Modern, responsive UI
- Three main tabs:
  - **Camera Management**: Connect/disconnect cameras, auto-discovery
  - **Recording Control**: Start/stop synchronized recording
  - **Download & Upload**: Download videos and upload to S3

## Prerequisites

### System Requirements
- **Python 3.8+** (for backend)
- **Node.js 16+** and npm (for frontend)
- **Bluetooth** enabled on your computer

### Platform-Specific Requirements

#### macOS
- No additional requirements

#### Windows
- WiFi management requires administrator privileges

#### Linux
- NetworkManager with `nmcli` installed
- BlueZ for Bluetooth support
- May require running with sudo for WiFi operations

## Installation

### 1. Clone or Navigate to the Project

```bash
cd desktop-app
```

### 2. Backend Setup

```bash
cd backend
pip install -r requirements.txt
```

**Note**: On macOS 15+ (Sequoia), you may need additional Bluetooth permissions. If you encounter issues, check System Settings > Privacy & Security > Bluetooth.

### 3. Frontend Setup

```bash
cd frontend
npm install
```

## Running the Application

### Development Mode

#### Option 1: Start Backend and Frontend Separately

**Terminal 1 - Start Backend:**
```bash
cd backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 - Start Frontend:**
```bash
cd frontend
npm run electron-dev
```

#### Option 2: Use the Provided Script (Recommended)

```bash
# From desktop-app root directory
chmod +x start.sh
./start.sh
```

The application will:
1. Start the FastAPI backend on http://127.0.0.1:8000
2. Launch the Electron app automatically
3. Open DevTools in development mode

### Production Build

```bash
cd frontend
npm run build
npm run package
```

This will create a distributable application in `frontend/dist/`.

## Usage Guide

### 1. Camera Management Tab

#### Adding Cameras Manually
1. Click "Add Camera"
2. Enter:
   - Camera Serial (last 4 digits)
   - Camera Name (optional, e.g., "Front Camera")
   - WiFi SSID (e.g., "GP25468881")
   - WiFi Password
3. Click "Add Camera"

#### Auto-Discovery
1. Ensure Bluetooth is enabled
2. Turn on your GoPro cameras
3. Click "Auto-Discover"
4. Wait 30 seconds for scanning
5. Discovered cameras will be logged in console
6. Add them manually with WiFi credentials

#### Connecting Cameras
1. Add all your cameras first
2. Click "Connect All Cameras"
3. Wait for BLE connections to establish
4. Connected cameras will show "Connected" badge

### 2. Recording Control Tab

#### Starting Recording
1. Ensure cameras are connected (green "Connected" status)
2. Click "Start Recording"
3. All cameras will begin recording simultaneously
4. Timer will show recording duration

#### Stopping Recording
1. Click "Stop Recording"
2. Wait a few seconds for files to save
3. Recording timer will reset

**Important**: Always wait at least 5-10 seconds after stopping before downloading to ensure files are saved.

### 3. Download & Upload Tab

#### Downloading Videos
1. After recording, click "Enable WiFi on All Cameras"
2. Wait 20 seconds for WiFi to activate
3. For each camera, click "Download All Files"
4. Progress will be shown in real-time
5. Files are downloaded in descending order (newest first)
6. Downloaded files are saved to `./gopro_downloads/`

#### Uploading to S3
1. Configure S3 settings at the top:
   - Backend URL (your upload endpoint)
   - API Key
2. Find the file you want to upload in the list
3. Click "Upload to S3" next to the file
4. Wait for upload to complete

## Configuration

### Camera Configuration Format

Cameras are stored with the following structure:
```json
{
  "serial": "8881",
  "name": "Front Camera",
  "wifi_ssid": "GP25468881",
  "wifi_password": "sW3-T!C-zMz"
}
```

### S3 Upload Configuration

The app uses a backend API for S3 uploads. Configure:
- **Backend URL**: Your upload endpoint
- **API Key**: Authentication key for your backend

Default configuration (can be changed in the UI):
```javascript
{
  backend_url: "https://tinify-backend-dev-868570596092.asia-south1.run.app/api/upload-file",
  api_key: "juniordevKey@9911"
}
```

## File Structure

```
desktop-app/
├── backend/
│   ├── main.py                 # FastAPI server
│   ├── camera_manager.py       # BLE camera control
│   ├── wifi_manager.py         # Cross-platform WiFi
│   ├── download_manager.py     # Download/upload logic
│   └── requirements.txt        # Python dependencies
├── frontend/
│   ├── electron/
│   │   ├── main.js            # Electron main process
│   │   └── preload.js         # Preload script
│   ├── src/
│   │   ├── components/
│   │   │   ├── CameraManagement.js
│   │   │   ├── RecordingControl.js
│   │   │   └── DownloadUpload.js
│   │   ├── App.js             # Main React component
│   │   └── index.js           # React entry point
│   ├── public/
│   │   └── index.html
│   └── package.json
├── gopro_downloads/           # Downloaded videos (created automatically)
└── README.md
```

## API Endpoints

### Camera Management
- `GET /api/cameras` - List all cameras
- `POST /api/cameras` - Add camera
- `DELETE /api/cameras/{serial}` - Remove camera
- `POST /api/cameras/discover` - Auto-discover cameras
- `POST /api/cameras/connect-all` - Connect all cameras
- `POST /api/cameras/disconnect-all` - Disconnect all cameras

### Recording Control
- `POST /api/recording/start` - Start recording
- `POST /api/recording/stop` - Stop recording

### WiFi & Download
- `GET /api/wifi/current` - Get current WiFi
- `POST /api/wifi/connect` - Connect to WiFi
- `POST /api/wifi/enable-all` - Enable WiFi on cameras
- `GET /api/media/list` - List media on camera
- `POST /api/download/{serial}` - Download from camera
- `GET /api/downloads/list` - List downloaded files
- `POST /api/upload` - Upload to S3

### WebSocket
- `ws://127.0.0.1:8000/ws` - Real-time updates

## Troubleshooting

### Bluetooth Connection Issues
- **macOS**: Check System Settings > Privacy & Security > Bluetooth
- **Windows**: Ensure Bluetooth is enabled in Device Manager
- **Linux**: Check BlueZ service is running: `sudo systemctl status bluetooth`

### WiFi Connection Issues
- **macOS**: May require Full Disk Access for Terminal/app
- **Windows**: Run application as Administrator
- **Linux**: May need sudo permissions for NetworkManager

### Camera Not Discovered
- Ensure camera is powered on
- Check Bluetooth is enabled on computer
- Try moving camera closer to computer
- Restart camera and try again

### Download Fails
- Ensure WiFi is enabled on camera (wait 20 seconds after enabling)
- Check you're not connected to another WiFi
- Verify camera WiFi credentials are correct
- Try connecting to camera WiFi manually first

### Upload Fails
- Check backend URL is correct and accessible
- Verify API key is valid
- Ensure file size is within backend limits
- Check internet connection

## Advanced Configuration

### Changing Default Ports

Backend port (default: 8000):
```python
# backend/main.py
uvicorn.run(app, host="127.0.0.1", port=8000)
```

Frontend will automatically connect to backend at `http://127.0.0.1:8000`

### Custom Download Directory

Downloads are saved to `./gopro_downloads/` by default. To change:

```python
# backend/download_manager.py
download_manager = DownloadManager(download_dir=Path("/your/custom/path"))
```

## Development

### Running Tests
```bash
# Backend tests (if available)
cd backend
pytest

# Frontend tests
cd frontend
npm test
```

### Building for Production
```bash
cd frontend
npm run build
npm run package
```

This creates platform-specific executables in `frontend/dist/`.

## Known Limitations

1. **WiFi Switching**: The app temporarily disconnects your computer from current WiFi to connect to GoPro WiFi
2. **Download Speed**: Limited by WiFi connection speed between computer and GoPro
3. **BLE Range**: Cameras must be within Bluetooth range (typically 10-30 feet)
4. **Simultaneous Downloads**: Downloads happen sequentially, not in parallel
5. **Platform Permissions**: May require elevated permissions for WiFi/Bluetooth on some platforms

## Contributing

Feel free to submit issues and enhancement requests!

## License

This project is based on the OpenGoPro SDK and follows its licensing terms.

## Credits

- Built with [open-gopro](https://github.com/gopro/OpenGoPro) Python SDK
- Uses [Electron](https://www.electronjs.org/) and [React](https://reactjs.org/)
- Backend powered by [FastAPI](https://fastapi.tiangolo.com/)
