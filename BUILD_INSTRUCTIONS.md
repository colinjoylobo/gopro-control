# GoPro Control Center - Build & Deployment Guide

## Current Architecture

**Type:** Electron Desktop Application
**Backend:** FastAPI (Python)
**Frontend:** React
**Package Manager:** electron-builder

When deployed, each user runs the app on their own machine with:
- Local FastAPI server (port 8000)
- Local file storage in `~/Documents/GoPro Downloads/`
- Direct BLE/WiFi connection to GoPro cameras

---

## File Storage in Production

Files are saved to:
```
macOS:   /Users/username/Documents/GoPro Downloads/2026-02-18_GoPro8881/
Windows: C:\Users\username\Documents\GoPro Downloads\2026-02-18_GoPro8881\
Linux:   /home/username/Documents/GoPro Downloads/2026-02-18_GoPro8881/
```

Each user has isolated storage on their own machine. No cloud storage needed.

---

## Build for Production

### Option 1: Simple Build (Requires User to Have Python)

**Pros:** Smaller package size
**Cons:** Users must install Python 3.9+ and dependencies

```bash
cd desktop-app/frontend

# Build React app
npm run build

# Package Electron app
npm run package
```

**Output:**
- macOS: `dist/GoPro Control-1.0.0.dmg`
- Windows: `dist/GoPro Control Setup 1.0.0.exe`
- Linux: `dist/GoPro Control-1.0.0.AppImage`

**User Setup Required:**
```bash
# Users need to install Python dependencies
cd backend
python3 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

---

### Option 2: Bundle Python (Recommended for Distribution)

**Pros:** Users get everything in one package
**Cons:** Larger package size (~200MB)

#### Using PyInstaller to Bundle Backend

1. **Install PyInstaller:**
```bash
cd desktop-app/backend
source venv/bin/activate
pip install pyinstaller
```

2. **Create standalone backend executable:**
```bash
pyinstaller --name gopro-backend \
  --onefile \
  --collect-all open_gopro \
  --collect-all bleak \
  --hidden-import=uvicorn.logging \
  --hidden-import=uvicorn.loops.auto \
  --hidden-import=uvicorn.protocols.http.auto \
  --hidden-import=uvicorn.protocols.websockets.auto \
  main.py
```

3. **Update electron/main.js to use bundled backend:**
```javascript
// Replace this:
pythonExecutable = path.join(backendPath, 'venv', 'bin', 'python3');

// With this (for production):
const backendExecutable = process.platform === 'darwin'
  ? path.join(__dirname, '../../backend/dist/gopro-backend')
  : path.join(__dirname, '../../backend/dist/gopro-backend.exe');

backendProcess = spawn(backendExecutable, [], {
  cwd: backendPath,
  shell: true
});
```

4. **Update package.json to include backend:**
```json
{
  "build": {
    "files": [
      "build/**/*",
      "electron/**/*",
      "../backend/dist/gopro-backend*"  // Add this
    ]
  }
}
```

5. **Build Electron app:**
```bash
cd desktop-app/frontend
npm run build
npm run package
```

---

## Distribution Methods

### 1. **Manual Distribution** (Current)
- Upload `.dmg` / `.exe` / `.AppImage` to file sharing service
- Users download and install

### 2. **Mac App Store**
- Requires Apple Developer account ($99/year)
- Need to sign and notarize app
- Update `package.json`:
```json
{
  "build": {
    "mac": {
      "category": "public.app-category.photography",
      "hardenedRuntime": true,
      "gatekeeperAssess": false,
      "entitlements": "build/entitlements.mac.plist"
    }
  }
}
```

### 3. **Auto-Update** (electron-updater)
```bash
npm install electron-updater
```

Add to `electron/main.js`:
```javascript
const { autoUpdater } = require('electron-updater');

app.whenReady().then(() => {
  autoUpdater.checkForUpdatesAndNotify();
});
```

---

## Cloud Deployment Alternative (Not Recommended)

If you wanted to deploy as a **web service** instead:

**Architecture Changes Needed:**
```
┌─────────────┐
│   Browser   │ ← Frontend (React)
└─────────────┘
       ↓
┌─────────────┐
│  Cloud VM   │ ← Backend (FastAPI)
│  - Ubuntu   │
│  - BLE USB  │ ← Need BLE dongle in cloud
│  - WiFi     │
└─────────────┘
```

**Problems:**
1. ❌ Cloud servers don't have BLE access (no Bluetooth hardware)
2. ❌ Can't connect to local GoPro cameras from cloud
3. ❌ File downloads would be slow (cloud → user's browser)
4. ❌ Multiple users would conflict on same cameras

**Verdict:** Desktop app is the **correct architecture** for this use case.

---

## Development vs Production

| Aspect | Development | Production |
|--------|-------------|-----------|
| Backend | `python -m uvicorn` | Bundled executable |
| Frontend | `npm start` (port 3000) | Built static files |
| File Storage | `./gopro_downloads` | `~/Documents/GoPro Downloads` |
| Distribution | Run from source | `.dmg` / `.exe` installer |

---

## Testing Production Build

### macOS:
```bash
# Build
cd desktop-app/frontend
npm run build
npm run package

# Test
open dist/mac/GoPro\ Control.app
```

### Windows:
```bash
# Build
npm run build
npm run package

# Test
.\dist\win-unpacked\GoPro Control.exe
```

---

## Current File Organization

When users download from cameras:

```
~/Documents/GoPro Downloads/
├── 2026-02-18_GoPro8881/
│   ├── GX010150.MP4
│   ├── GX010151.MP4
│   └── GX010152.MP4
└── 2026-02-18_GoPro2152/
    ├── GX010142.MP4
    └── GX010143.MP4
```

ZIP uploads create:
```
S3/Azure Storage:
└── zips/
    ├── 2026-02-18_GoPro8881.zip
    └── 2026-02-18_GoPro2152.zip
```

---

## Security Considerations

1. **API Keys:** Currently stored in frontend. Consider:
   - Electron IPC to store in backend
   - Use electron-store for encrypted storage
   - Prompt user on first launch

2. **Permissions:**
   - macOS: Bluetooth permission (add to Info.plist)
   - macOS: WiFi permission (add to Info.plist)

3. **Signing:**
   - macOS: Sign with Apple Developer certificate
   - Windows: Sign with Code Signing certificate

---

## Next Steps for Production

1. ✅ Files now save to `~/Documents/GoPro Downloads/`
2. ⏳ Bundle Python backend with PyInstaller
3. ⏳ Add app signing for macOS/Windows
4. ⏳ Test on clean machine without Python installed
5. ⏳ Create installer with custom branding
6. ⏳ Add auto-update functionality

---

## Support & Troubleshooting

**Issue:** "Backend failed to start"
- Check if port 8000 is available
- Check Python installation
- Check backend logs in Console

**Issue:** "Files not found"
- Check `~/Documents/GoPro Downloads/` directory
- Verify disk space available
- Check write permissions

**Issue:** "Cannot connect to cameras"
- Verify Bluetooth permissions granted
- Check WiFi permissions granted
- Ensure cameras are in pairing mode
