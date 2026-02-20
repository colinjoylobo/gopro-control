# GoPro BLE Connection Troubleshooting

## The Problem
Your cameras are being discovered but timing out during connection:
```
Connection request timed out
Failed to connect. Retrying #1
```

## Quick Fix - Reset Camera Bluetooth Pairing

### Method 1: Reset Camera Connections (Recommended)
On **each GoPro camera**:

1. **Power ON** the camera
2. Swipe down from top to open settings
3. Go to **Connections > Reset Connections**
4. Confirm the reset
5. Camera will restart
6. Power it back ON
7. **Keep the camera on the home screen** (don't go into menus)

### Method 2: Factory Reset Bluetooth Only
On **each GoPro camera**:

1. Swipe down → **Preferences**
2. Go to **Connections**
3. Select **Device Connections**
4. **Forget all paired devices**
5. Go back to home screen

### Method 3: Clear macOS Bluetooth Cache (if above doesn't work)
On **your Mac**:

```bash
# Stop Bluetooth
sudo killall -HUP bluetoothd

# Wait 5 seconds, then turn Bluetooth OFF and ON in System Settings
```

## After Resetting

1. Make sure cameras are **powered ON**
2. Keep cameras on the **home screen** (not in menus)
3. In the desktop app, go to **Camera Management** tab
4. Click **"Auto-Discover"** to find cameras
5. Click on discovered camera to add it (you'll need WiFi password)
6. Click **"Connect All Cameras"**
7. Watch terminal - connection should succeed in 5-10 seconds

## Expected Terminal Output (Success)
```
[1874] Connecting BLE...
[1874] Opening connection (timeout: 60s)...
Opening the camera connection...
Establishing BLE connection to 6DA1523D-29A5-3F87-9A8B-5DC218849082: GoPro 1874...
Connected successfully!
[1874] ✅ BLE connected successfully
```

## Important Notes

1. **Only connect to ONE camera at first** to test
2. Camera must be **on home screen**, not recording or in menus
3. Camera should **not be connected** to GoPro mobile app
4. If you see "Connection request timed out", the camera is not ready:
   - Reset connections on the camera
   - Power cycle the camera
   - Make sure no other device is connected to it

## Still Not Working?

Try the working Python script to verify BLE works:
```bash
cd /Users/joel/Downloads/OpenGoPro
python3 final_working_script-gopro.py
```

If the Python script works but the desktop app doesn't, there may be a Bluetooth permission issue with Electron.
