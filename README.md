# GoPro Control Center

A cross-platform desktop application for controlling multiple GoPro cameras simultaneously over COHN (Camera on Home Network) and BLE. Built with Electron, React, and FastAPI.

## Features

- **Multi-Camera Control** -- connect, configure, and record with multiple GoPros from one screen
- **COHN (Camera on Home Network)** -- provision cameras onto your WiFi for always-on HTTP access without BLE range limits
- **Synchronized Recording** -- start/stop recording on all cameras at once with shoot and take management
- **Live Preview** -- real-time H.265 streams from each camera via COHN with sub-second latency
- **Bulk Download** -- download files from all cameras in parallel over COHN (falls back to WiFi-direct)
- **Preset Management** -- capture camera settings as presets and apply them across cameras via BLE or COHN
- **S3 Upload** -- upload individual files or auto-zipped camera bundles to S3
- **Health Dashboard** -- battery levels, storage, and connection status updated in real-time
- **Cross-Platform** -- macOS, Windows, and Linux

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.8+ | Backend server |
| Node.js | 16+ | Frontend build |
| npm | (bundled with Node) | Package management |
| ffmpeg | any | Live preview transcoding |
| Bluetooth | -- | BLE camera discovery and pairing |

### One-Command Setup (builds and installs the app)

**macOS / Linux:**
```bash
git clone <repo-url> && cd gopro-control-v2
chmod +x setup.sh
./setup.sh
```

**Windows (PowerShell as Administrator):**
```powershell
git clone <repo-url>; cd gopro-control-v2
powershell -ExecutionPolicy Bypass -File setup.ps1
```

The setup script will:
1. Detect your OS and install missing prerequisites (Python, Node, ffmpeg, git)
2. Create a Python venv and install backend dependencies
3. Bundle the backend with PyInstaller
4. Build the React frontend
5. Package the Electron app and install it

### Development Mode

For active development, use the launcher scripts instead -- they start the backend and frontend without bundling.

**macOS / Linux:**
```bash
chmod +x start.sh
./start.sh
```

**Windows:**
```bat
start.bat
```

This starts:
- FastAPI backend on `http://127.0.0.1:8000`
- React dev server on `http://localhost:3000`
- Electron window pointing at the dev server

## Architecture

```
Electron (main process)
  |
  +-- React UI (renderer)          Tabs: Cameras | Recording | Preview | Downloads
  |     |
  |     +-- axios / WebSocket -----> FastAPI backend (port 8000)
  |                                    |
  |                                    +-- BLE (open-gopro) ----> GoPro cameras
  |                                    +-- COHN (HTTPS) --------> GoPro cameras on WiFi
  |                                    +-- WiFi-direct ----------> GoPro AP mode
  |                                    +-- ffmpeg (UDP->HTTP) --> Live preview streams
```

**Backend** (`backend/`) -- FastAPI + Python
- `main.py` -- all API routes, WebSocket hub, background health monitor
- `camera_manager.py` -- BLE connection management via open-gopro
- `cohn_manager.py` -- COHN provisioning, credential storage, IP recovery
- `download_manager.py` -- file download (COHN/WiFi), S3 upload, ZIP packaging
- `wifi_manager.py` -- cross-platform WiFi switching (macOS/Windows/Linux)
- `shoot_manager.py` -- shoot and take tracking
- `preset_manager.py` -- camera preset CRUD and application

**Frontend** (`frontend/`) -- Electron + React
- `CameraManagement.js` -- add/discover/connect cameras, COHN provisioning
- `RecordingDashboard.js` -- synchronized recording with shoot/take management
- `LivePreview.js` -- real-time camera streams via mpegts.js
- `DownloadUpload.js` -- browse/download/upload media, SD card management
- `PresetManager.js` -- capture, edit, and apply camera presets

## COHN (Camera on Home Network)

COHN lets GoPro cameras join your WiFi network so the app can communicate over HTTP instead of BLE/WiFi-direct. This enables:

- **No range limits** -- cameras just need to be on the same network
- **Parallel downloads** -- download from all cameras simultaneously
- **Live preview** -- stream from multiple cameras at once
- **Always-on settings** -- apply presets and check status without BLE pairing

### Provisioning a Camera

1. Go to the **Cameras** tab
2. Connect the camera via BLE (click "Connect")
3. Click **Provision COHN** on the camera card
4. The camera will join your WiFi network and get an IP address
5. Once provisioned, a green COHN badge appears -- the camera is now accessible over HTTP

### Network Management

- The app stores COHN credentials per-network so you can switch locations
- If a camera's IP changes (DHCP), the app uses ARP to rediscover it
- Manual IP override is available via the API if auto-discovery fails

## File Structure

```
gopro-control-v2/
+-- backend/
|   +-- main.py                  # FastAPI server (all routes)
|   +-- camera_manager.py        # BLE camera control
|   +-- cohn_manager.py          # COHN provisioning and management
|   +-- download_manager.py      # Download/upload/ZIP logic
|   +-- wifi_manager.py          # Cross-platform WiFi
|   +-- shoot_manager.py         # Shoot and take tracking
|   +-- preset_manager.py        # Preset CRUD
|   +-- gopro-backend.spec       # PyInstaller spec
|   +-- requirements.txt         # Pinned Python dependencies
|   +-- venv/                    # Python virtual environment (gitignored)
+-- frontend/
|   +-- electron/
|   |   +-- main.js              # Electron main process
|   |   +-- preload.js           # Preload script
|   +-- src/
|   |   +-- App.js               # Main React component (tabs, WebSocket)
|   |   +-- components/
|   |   |   +-- CameraManagement.js
|   |   |   +-- RecordingDashboard.js
|   |   |   +-- LivePreview.js
|   |   |   +-- DownloadUpload.js
|   |   |   +-- PresetManager.js
|   |   +-- App.css
|   +-- public/
|   +-- package.json
|   +-- package-lock.json        # Locked dependency tree
+-- setup.sh                     # One-step setup (macOS/Linux)
+-- setup.ps1                    # One-step setup (Windows)
+-- build.sh                     # Build distributable (macOS/Linux)
+-- build.bat                    # Build distributable (Windows)
+-- start.sh                     # Dev launcher (macOS/Linux)
+-- start.bat                    # Dev launcher (Windows)
+-- saved_cameras.json           # Persisted camera list
+-- cohn_credentials.json        # COHN provisioning data
+-- camera_presets.json          # Saved presets
+-- shoots.json                  # Shoot/take history
+-- README.md
```

## API Endpoints

### Camera Management
| Method | Path | Description |
|---|---|---|
| GET | `/api/cameras` | List all cameras |
| POST | `/api/cameras` | Add a camera |
| DELETE | `/api/cameras/{serial}` | Remove a camera |
| PATCH | `/api/cameras/{serial}` | Rename a camera |
| POST | `/api/cameras/discover` | BLE auto-discover |
| POST | `/api/cameras/connect-all` | Connect all via BLE |
| POST | `/api/cameras/connect/{serial}` | Connect one via BLE |
| POST | `/api/cameras/disconnect-all` | Disconnect all |
| POST | `/api/cameras/disconnect/{serial}` | Disconnect one |
| POST | `/api/cameras/check-connections` | Reconnect existing BLE |
| GET | `/api/cameras/battery` | Battery levels |

### Recording & Shoots
| Method | Path | Description |
|---|---|---|
| POST | `/api/recording/start` | Start recording (all cameras) |
| POST | `/api/recording/stop` | Stop recording |
| POST | `/api/shoots` | Create a shoot |
| GET | `/api/shoots` | List shoots |
| GET | `/api/shoots/active` | Get active shoot |
| POST | `/api/shoots/active` | Set active shoot |
| POST | `/api/shoots/deactivate` | End current shoot |
| DELETE | `/api/shoots/{id}` | Delete a shoot |
| POST | `/api/shoots/{id}/takes` | Create a take |
| PATCH | `/api/shoots/{id}/takes/{n}` | Update a take |
| GET | `/api/shoots/{id}/takes/{n}/files` | Get take files |
| DELETE | `/api/shoots/{id}/takes/{n}` | Delete a take |

### Live Preview
| Method | Path | Description |
|---|---|---|
| POST | `/api/preview/start` | Start preview (all cameras) |
| POST | `/api/preview/start/{serial}` | Start preview (one) |
| POST | `/api/preview/stop` | Stop preview (all) |
| POST | `/api/preview/stop/{serial}` | Stop preview (one) |
| POST | `/api/preview/stream-start` | Start UDP/HLS stream |

### COHN
| Method | Path | Description |
|---|---|---|
| POST | `/api/cohn/provision/{serial}` | Provision camera for COHN |
| DELETE | `/api/cohn/provision/{serial}` | Remove COHN credentials |
| GET | `/api/cohn/status` | COHN status (all cameras) |
| GET | `/api/cohn/status/{serial}` | COHN status (one) |
| POST | `/api/cohn/reenable` | Re-enable COHN (all) |
| POST | `/api/cohn/reenable/{serial}` | Re-enable COHN (one) |
| GET | `/api/cohn/networks` | List saved WiFi networks |
| POST | `/api/cohn/networks/switch` | Switch WiFi network |
| PATCH | `/api/cohn/camera/{serial}/ip` | Update stored IP |
| GET | `/api/cohn/stream/{serial}` | MPEG-TS stream (chunked HTTP) |
| POST | `/api/cohn/snapshot/all` | Capture JPEG from all cameras |
| POST | `/api/cohn/preview/start` | Start COHN preview (all) |
| POST | `/api/cohn/preview/stop` | Stop COHN preview (all) |
| POST | `/api/cohn/settings/apply` | Apply settings via COHN |
| POST | `/api/cohn/gps/enable` | Enable GPS via COHN |
| GET | `/api/cohn/camera/state/{serial}` | Full camera state via COHN |

### Downloads & Media
| Method | Path | Description |
|---|---|---|
| GET | `/api/media/list` | List media on camera |
| POST | `/api/browse/{serial}` | Browse SD card |
| GET | `/api/cameras/{serial}/media-summary` | Media summary |
| POST | `/api/download/{serial}` | Download all files |
| POST | `/api/download/{serial}/latest` | Download latest video |
| POST | `/api/download/{serial}/selected` | Download selected files |
| POST | `/api/download/shoot/{id}` | Download all takes (parallel COHN) |
| POST | `/api/cameras/{serial}/erase-sd` | Erase SD card |
| GET | `/api/downloads/list` | List downloaded files |
| POST | `/api/upload` | Upload file to S3 |
| POST | `/api/create-zip` | ZIP and upload to S3 |
| POST | `/api/upload-camera-bulk/{serial}` | ZIP all camera files and upload |

### Presets
| Method | Path | Description |
|---|---|---|
| GET | `/api/presets` | List presets |
| POST | `/api/presets` | Create/update preset |
| POST | `/api/presets/capture/{serial}` | Capture settings from camera |
| POST | `/api/presets/{name}/apply` | Apply preset (BLE) |
| POST | `/api/presets/{name}/apply-cohn` | Apply preset (COHN) |
| DELETE | `/api/presets/{name}` | Delete preset |
| PATCH | `/api/presets/{name}` | Toggle pinned |

### Health & WebSocket
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/health/dashboard` | Full health dashboard |
| GET | `/api/health/{serial}` | Camera health detail |
| POST | `/api/test-s3-backend` | Test S3 connectivity |
| WS | `/ws` | Real-time updates |

## Building for Distribution

**macOS / Linux:**
```bash
./build.sh
```

**Windows:**
```bat
build.bat
```

This produces:
- **macOS**: `frontend/dist/mac-arm64/GoPro Control.app` (or `.dmg`)
- **Windows**: `frontend/dist/GoPro Control Setup 1.0.0.exe`
- **Linux**: `frontend/dist/GoPro Control-1.0.0.AppImage`

The build bundles Python via PyInstaller so end users don't need Python installed.

## Troubleshooting

### BLE / Bluetooth
- **macOS**: System Settings > Privacy & Security > Bluetooth -- ensure your terminal/app has permission
- **Windows**: Bluetooth must be enabled in Device Manager; run as Administrator if discovery fails
- **Linux**: BlueZ must be running (`sudo systemctl start bluetooth`) and user must be in the `bluetooth` group

### WiFi
- **macOS**: Terminal may need Full Disk Access for WiFi switching
- **Windows**: WiFi management requires Administrator privileges
- **Linux**: NetworkManager (`nmcli`) must be installed; may need sudo

### COHN
- Camera must be on firmware supporting COHN (Hero 12+)
- Camera and computer must be on the same WiFi network
- If COHN status shows "offline", try **Re-enable COHN** (re-provisions via BLE)
- If IP changed after a router reboot, the app will attempt ARP recovery; if that fails, update the IP manually

### Live Preview
- Requires `ffmpeg` installed and on PATH
- Preview uses UDP streaming -- firewalls must allow UDP traffic on the local network
- If preview shows black, try stopping and restarting it

### Downloads
- COHN downloads work in parallel; WiFi-direct downloads are sequential (one camera at a time)
- Wait 5-10 seconds after stopping recording before downloading so files finalize
- If a download stalls, check camera WiFi/COHN status

## Credits

- [open-gopro](https://github.com/gopro/OpenGoPro) Python SDK
- [Electron](https://www.electronjs.org/) + [React](https://reactjs.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [mpegts.js](https://github.com/nicedayzhu/mpegts.js) for live preview decoding
