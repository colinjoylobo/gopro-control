# Quick Start Guide

## 5-Minute Setup

### Step 1: Install Dependencies

**Backend:**
```bash
cd desktop-app/backend
pip install -r requirements.txt
```

**Frontend:**
```bash
cd desktop-app/frontend
npm install
```

### Step 2: Run the Application

**Easiest Method:**
```bash
cd desktop-app
./start.sh          # macOS/Linux
# OR
start.bat           # Windows
```

**Manual Method:**

Terminal 1:
```bash
cd desktop-app/backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Terminal 2:
```bash
cd desktop-app/frontend
npm run electron-dev
```

### Step 3: Add Your Cameras

1. Open the app (it starts on "Camera Management" tab)
2. Click "Add Camera"
3. Fill in:
   - Serial: `8881` (last 4 digits from your GoPro)
   - Name: `Front Camera` (optional)
   - WiFi SSID: `GP25468881` (your GoPro's WiFi name)
   - WiFi Password: `sW3-T!C-zMz` (from GoPro WiFi settings)
4. Click "Add Camera"
5. Repeat for all cameras

### Step 4: Connect to Cameras

1. Make sure all GoPros are powered on
2. Click "Connect All Cameras"
3. Wait for BLE connections (10-30 seconds)
4. You'll see "Connected" badges on each camera

### Step 5: Record Videos

1. Go to "Recording Control" tab
2. Click "Start Recording"
3. All cameras record simultaneously
4. Click "Stop Recording" when done
5. Wait 5-10 seconds for files to save

### Step 6: Download Videos

1. Go to "Download & Upload" tab
2. Click "Enable WiFi on All Cameras"
3. Wait 20 seconds
4. Click "Download All Files" for each camera
5. Videos download in order (newest first)

### Step 7: Upload to S3 (Optional)

1. Configure S3 settings at top of Download tab:
   - Backend URL: Your upload endpoint
   - API Key: Your API key
2. Click "Upload to S3" next to any downloaded file

## Common Issues

**Can't connect to cameras?**
- Enable Bluetooth on your computer
- Move cameras closer
- Restart cameras

**Download fails?**
- Wait 20 seconds after enabling WiFi
- Make sure recording is stopped
- Check WiFi credentials are correct

**App won't start?**
- Check Python 3.8+ is installed: `python3 --version`
- Check Node.js 16+ is installed: `node --version`
- Kill any process on port 8000: `lsof -ti:8000 | xargs kill -9`

## File Locations

- **Downloaded videos**: `desktop-app/gopro_downloads/`
- **Backend logs**: Check terminal running backend
- **Frontend logs**: Check Electron DevTools console

## Next Steps

See the full [README.md](README.md) for:
- Detailed API documentation
- Advanced configuration
- Troubleshooting guide
- Platform-specific requirements
