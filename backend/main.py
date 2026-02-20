"""
FastAPI Backend for GoPro Desktop App
"""
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import asyncio
import logging
from pathlib import Path

from camera_manager import CameraManager
from wifi_manager import WiFiManager
from download_manager import DownloadManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(title="GoPro Desktop App API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Managers
camera_manager = CameraManager()
wifi_manager = WiFiManager()
download_manager = DownloadManager()

# WebSocket connections for real-time updates
websocket_connections: List[WebSocket] = []

# Background task control
background_monitor_task = None
monitor_running = False


# ============== Models ==============

class CameraModel(BaseModel):
    serial: str
    wifi_ssid: str
    wifi_password: str
    name: Optional[str] = ""


class WiFiConnectionModel(BaseModel):
    ssid: str
    password: str


class UploadModel(BaseModel):
    file_path: str
    serial: str
    backend_url: str
    api_key: str


class CreateZipModel(BaseModel):
    file_paths: List[str]
    backend_url: str
    api_key: str
    zip_name: Optional[str] = None


# ============== WebSocket ==============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.append(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_connections.remove(websocket)


async def broadcast_message(message: dict):
    """Broadcast message to all connected clients"""
    for connection in websocket_connections:
        try:
            await connection.send_json(message)
        except:
            pass


async def connection_monitor():
    """Background task that continuously monitors BLE connection status"""
    global monitor_running
    monitor_running = True

    # Track previous state
    previous_states = {}

    logger.info("🔄 Connection monitor started - checking every 0.5 seconds")

    while monitor_running:
        try:
            # Check connection status for all cameras
            for serial, camera in camera_manager.cameras.items():
                # Get current actual status
                current_connected = camera.update_connection_status()

                # Check if status changed
                previous_connected = previous_states.get(serial, None)

                if previous_connected is None:
                    # First time seeing this camera, just store state
                    previous_states[serial] = current_connected
                elif previous_connected != current_connected:
                    # Status changed! Broadcast update
                    logger.info(f"📡 INSTANT: Connection status changed for {serial}: {previous_connected} → {current_connected}")

                    await broadcast_message({
                        "type": "camera_connection",
                        "serial": serial,
                        "connected": current_connected
                    })

                    # Update previous state
                    previous_states[serial] = current_connected

            # Wait 0.5 seconds before next check (checks 2x per second)
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Connection monitor error: {e}")
            await asyncio.sleep(0.5)

    logger.info("🛑 Connection monitor stopped")


async def auto_detect_connections():
    """Background task to auto-detect existing BLE connections on startup"""
    # Wait a bit for everything to initialize
    await asyncio.sleep(3)

    if len(camera_manager.cameras) > 0:
        logger.info("=" * 60)
        logger.info("🔍 AUTO-DETECTING existing BLE connections...")
        logger.info(f"Checking {len(camera_manager.cameras)} camera(s)...")
        logger.info("=" * 60)

        try:
            # Check for existing connections
            results = await camera_manager.check_existing_connections()

            connected_count = sum(1 for c in results.values() if c)

            if connected_count > 0:
                logger.info(f"✅ AUTO-DETECTED {connected_count} existing connection(s)!")

                # Broadcast to all connected frontends
                for serial, connected in results.items():
                    if connected:
                        await broadcast_message({
                            "type": "camera_connection",
                            "serial": serial,
                            "connected": True
                        })
                        logger.info(f"   📡 {serial}: Connected!")
            else:
                logger.info("No existing connections found")

            logger.info("=" * 60)
        except Exception as e:
            logger.error(f"Auto-detection failed: {e}")


async def load_saved_cameras():
    """Load cameras from saved_cameras.json on startup"""
    import json
    from pathlib import Path

    saved_cameras_file = Path(__file__).parent.parent.parent / "saved_cameras.json"

    if saved_cameras_file.exists():
        try:
            logger.info("📁 Loading saved cameras from saved_cameras.json...")

            with open(saved_cameras_file, 'r') as f:
                data = json.load(f)

            cameras = data.get("cameras", [])

            if cameras:
                logger.info(f"Found {len(cameras)} saved camera(s)")

                for camera_data in cameras:
                    serial = camera_data.get("serial")
                    name = camera_data.get("name", f"GoPro {serial}")
                    wifi_ssid = camera_data.get("wifi_ssid")
                    wifi_password = camera_data.get("wifi_password")

                    success = camera_manager.add_camera(serial, wifi_ssid, wifi_password, name)

                    if success:
                        logger.info(f"   ✅ Loaded: {name} ({serial})")
                    else:
                        logger.warning(f"   ⚠️  {serial} already exists, skipping")

                logger.info(f"✅ Loaded {len(cameras)} camera(s) from saved_cameras.json")
            else:
                logger.info("No cameras found in saved_cameras.json")

        except Exception as e:
            logger.error(f"Failed to load saved_cameras.json: {e}")
    else:
        logger.info(f"No saved_cameras.json found at {saved_cameras_file}")
        logger.info("Cameras will need to be added manually via the UI or add_saved_cameras.py")


@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    global background_monitor_task

    logger.info("=" * 60)
    logger.info("🚀 Starting GoPro Desktop App Backend")
    logger.info("=" * 60)

    # STEP 1: Load saved cameras from JSON file
    await load_saved_cameras()

    # STEP 2: Start connection monitor
    background_monitor_task = asyncio.create_task(connection_monitor())
    logger.info("✅ Background connection monitor started")

    # STEP 3: Auto-detect existing BLE connections
    asyncio.create_task(auto_detect_connections())
    logger.info("✅ Auto-detection task scheduled")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    global monitor_running, background_monitor_task

    logger.info("=" * 60)
    logger.info("🛑 Shutting down GoPro Desktop App Backend")
    logger.info("=" * 60)

    # Stop monitor
    monitor_running = False
    if background_monitor_task:
        background_monitor_task.cancel()
        try:
            await background_monitor_task
        except asyncio.CancelledError:
            pass

    logger.info("✅ Background tasks stopped")


# ============== Camera Management ==============

@app.get("/api/cameras")
async def list_cameras():
    """Get list of all cameras"""
    return {"cameras": camera_manager.list_cameras()}


@app.post("/api/cameras")
async def add_camera(camera: CameraModel):
    """Add a new camera"""
    success = camera_manager.add_camera(
        camera.serial,
        camera.wifi_ssid,
        camera.wifi_password,
        camera.name
    )
    if success:
        await broadcast_message({"type": "camera_added", "camera": camera.dict()})
        return {"success": True, "message": "Camera added"}
    else:
        raise HTTPException(status_code=400, detail="Camera already exists")


@app.delete("/api/cameras/{serial}")
async def remove_camera(serial: str):
    """Remove a camera"""
    success = camera_manager.remove_camera(serial)
    if success:
        await broadcast_message({"type": "camera_removed", "serial": serial})
        return {"success": True, "message": "Camera removed"}
    else:
        raise HTTPException(status_code=404, detail="Camera not found")


@app.post("/api/cameras/discover")
async def discover_cameras(timeout: int = 30):
    """Auto-discover GoPro cameras"""
    try:
        discovered = await camera_manager.discover_cameras(timeout)
        return {"cameras": discovered}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/connect-all")
async def connect_all_cameras():
    """Connect to all cameras via BLE - checks existing connections FIRST for instant detection"""
    try:
        logger.info("=" * 60)
        logger.info("Starting BLE connection to all cameras...")
        logger.info(f"Total cameras to connect: {len(camera_manager.cameras)}")

        for serial in camera_manager.cameras.keys():
            logger.info(f"  - Camera {serial}")

        if len(camera_manager.cameras) == 0:
            logger.warning("No cameras configured!")
            raise HTTPException(status_code=400, detail="No cameras configured. Add cameras first.")

        logger.info("=" * 60)

        # STEP 1: FIRST check for existing macOS Bluetooth connections (INSTANT!)
        logger.info("STEP 1: Checking for existing macOS Bluetooth connections...")
        existing_results = await camera_manager.check_existing_connections()

        connected_count = sum(1 for c in existing_results.values() if c)
        logger.info(f"✅ Found {connected_count} existing connection(s)")

        # Broadcast updates for cameras that are already connected
        for serial, connected in existing_results.items():
            if connected:
                await broadcast_message({
                    "type": "camera_connection",
                    "serial": serial,
                    "connected": True
                })

        # STEP 2: Connect to remaining cameras that weren't already connected
        logger.info("STEP 2: Connecting to remaining cameras...")
        results = await camera_manager.connect_all()

        logger.info("=" * 60)
        logger.info("BLE Connection Results:")
        for serial, success in results.items():
            status = "✅ SUCCESS" if success else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
        logger.info("=" * 60)

        # Broadcast status updates
        for serial, success in results.items():
            await broadcast_message({
                "type": "camera_connection",
                "serial": serial,
                "connected": success
            })

        success_count = sum(1 for s in results.values() if s)
        total_count = len(results)

        logger.info(f"Final: {success_count}/{total_count} cameras connected successfully")

        return {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Connect all cameras failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/disconnect-all")
async def disconnect_all_cameras():
    """Disconnect all cameras"""
    try:
        await camera_manager.disconnect_all()
        return {"success": True, "message": "All cameras disconnected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/check-connections")
async def check_existing_connections():
    """Check for and reconnect to existing BLE connections"""
    try:
        logger.info("Checking for existing BLE connections...")
        results = await camera_manager.check_existing_connections()

        # Broadcast updates for any cameras that were reconnected
        for serial, connected in results.items():
            if connected:
                await broadcast_message({
                    "type": "camera_connection",
                    "serial": serial,
                    "connected": True
                })

        connected_count = sum(1 for c in results.values() if c)

        return {
            "success": True,
            "results": results,
            "connected_count": connected_count,
            "cameras": camera_manager.list_cameras()
        }
    except Exception as e:
        logger.error(f"Check connections failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Recording Control ==============

@app.post("/api/recording/start")
async def start_recording():
    """Start recording on all connected cameras"""
    try:
        logger.info("=" * 60)
        logger.info("🔴 START RECORDING REQUEST")

        connected = [cam for cam in camera_manager.cameras.values() if cam.connected]
        logger.info(f"Connected cameras: {len(connected)}/{len(camera_manager.cameras)}")

        if len(connected) == 0:
            logger.warning("No cameras connected!")
            raise HTTPException(status_code=400, detail="No cameras connected. Connect cameras first.")

        for cam in connected:
            logger.info(f"  - {cam.serial}: ready to record")

        logger.info("=" * 60)

        results = await camera_manager.start_recording_all()

        logger.info("=" * 60)
        logger.info("Recording Start Results:")
        for serial, success in results.items():
            status = "✅ RECORDING" if success else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
        logger.info("=" * 60)

        # Broadcast updates
        for serial, success in results.items():
            await broadcast_message({
                "type": "recording_started",
                "serial": serial,
                "success": success
            })

        success_count = sum(1 for s in results.values() if s)
        logger.info(f"Final: {success_count}/{len(results)} cameras recording")

        return {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Start recording failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/recording/stop")
async def stop_recording():
    """Stop recording on all cameras"""
    try:
        logger.info("=" * 60)
        logger.info("⏹️  STOP RECORDING REQUEST")

        recording = [cam for cam in camera_manager.cameras.values() if cam.recording]
        logger.info(f"Recording cameras: {len(recording)}")

        if len(recording) == 0:
            logger.warning("No cameras are currently recording!")

        for cam in recording:
            logger.info(f"  - {cam.serial}: stopping...")

        logger.info("=" * 60)

        results = await camera_manager.stop_recording_all()

        logger.info("=" * 60)
        logger.info("Recording Stop Results:")
        for serial, success in results.items():
            status = "✅ STOPPED" if success else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
        logger.info("=" * 60)

        # Broadcast updates
        for serial, success in results.items():
            await broadcast_message({
                "type": "recording_stopped",
                "serial": serial,
                "success": success
            })

        success_count = sum(1 for s in results.values() if s)
        logger.info(f"Final: {success_count}/{len(results)} cameras stopped")
        logger.info("Waiting 5 seconds for files to save...")

        return {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stop recording failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Live Preview / Webcam ==============

@app.post("/api/preview/start")
async def start_preview():
    """Start live preview/webcam mode on all connected cameras"""
    try:
        logger.info("=" * 60)
        logger.info("📹 START PREVIEW REQUEST")

        connected = [cam for cam in camera_manager.cameras.values() if cam.connected]
        logger.info(f"Connected cameras: {len(connected)}/{len(camera_manager.cameras)}")

        if len(connected) == 0:
            logger.warning("No cameras connected!")
            raise HTTPException(status_code=400, detail="No cameras connected. Connect cameras first.")

        for cam in connected:
            logger.info(f"  - {cam.serial}: starting preview...")

        logger.info("=" * 60)

        results = await camera_manager.start_preview_all()

        logger.info("=" * 60)
        logger.info("Preview Start Results:")
        for serial, result in results.items():
            status = "✅ STREAMING" if result.get("success") else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
            if result.get("stream_url"):
                logger.info(f"    Stream URL: {result['stream_url']}")
        logger.info("=" * 60)

        # Broadcast updates
        for serial, result in results.items():
            await broadcast_message({
                "type": "preview_started",
                "serial": serial,
                "success": result.get("success"),
                "stream_url": result.get("stream_url"),
                "preview_url": result.get("preview_url")
            })

        success_count = sum(1 for r in results.values() if r.get("success"))
        logger.info(f"Final: {success_count}/{len(results)} cameras streaming")

        return {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Start preview failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/preview/start/{serial}")
async def start_preview_single(serial: str):
    """Start live preview on a specific camera"""
    try:
        logger.info("=" * 60)
        logger.info(f"📹 START PREVIEW REQUEST for camera {serial}")

        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        if not camera.connected:
            raise HTTPException(status_code=400, detail=f"Camera {serial} is not connected. Connect via BLE first.")

        logger.info(f"Starting preview on {serial}...")
        result = await camera.start_webcam()

        status = "✅ STREAMING" if result.get("success") else "❌ FAILED"
        logger.info(f"Preview result: {status}")
        if result.get("stream_url"):
            logger.info(f"Stream URL: {result['stream_url']}")
        logger.info("=" * 60)

        # Broadcast update
        await broadcast_message({
            "type": "preview_started",
            "serial": serial,
            "success": result.get("success"),
            "stream_url": result.get("stream_url"),
            "preview_url": result.get("preview_url")
        })

        return {
            "success": result.get("success", False),
            "serial": serial,
            "stream_url": result.get("stream_url"),
            "preview_url": result.get("preview_url"),
            "wifi_ssid": camera.wifi_ssid,
            "camera": camera.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Start preview failed for {serial}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/preview/stop/{serial}")
async def stop_preview_single(serial: str):
    """Stop live preview on a specific camera"""
    try:
        logger.info("=" * 60)
        logger.info(f"⏹️  STOP PREVIEW REQUEST for camera {serial}")

        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        success = await camera.stop_webcam()

        status = "✅ STOPPED" if success else "❌ FAILED"
        logger.info(f"Preview stop result: {status}")
        logger.info("=" * 60)

        # Broadcast update
        await broadcast_message({
            "type": "preview_stopped",
            "serial": serial,
            "success": success
        })

        return {
            "success": success,
            "serial": serial
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stop preview failed for {serial}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/preview/stop")
async def stop_preview():
    """Stop live preview/webcam mode on all cameras"""
    try:
        logger.info("=" * 60)
        logger.info("⏹️  STOP PREVIEW REQUEST")

        results = await camera_manager.stop_preview_all()

        logger.info("=" * 60)
        logger.info("Preview Stop Results:")
        for serial, success in results.items():
            status = "✅ STOPPED" if success else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
        logger.info("=" * 60)

        # Broadcast updates
        for serial, success in results.items():
            await broadcast_message({
                "type": "preview_stopped",
                "serial": serial,
                "success": success
            })

        success_count = sum(1 for s in results.values() if s)
        logger.info(f"Final: {success_count}/{len(results)} cameras stopped")

        return {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stop preview failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== WiFi Management ==============

@app.get("/api/wifi/current")
async def get_current_wifi():
    """Get current WiFi SSID"""
    ssid = wifi_manager.get_current_wifi()
    return {"ssid": ssid}


@app.post("/api/wifi/connect")
async def connect_wifi(connection: WiFiConnectionModel):
    """Connect to a WiFi network"""
    try:
        success = wifi_manager.connect_wifi(connection.ssid, connection.password)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wifi/enable-all")
async def enable_wifi_all():
    """Enable WiFi on all cameras"""
    try:
        logger.info("=" * 60)
        logger.info("📡 ENABLE WiFi REQUEST")

        connected = [cam for cam in camera_manager.cameras.values() if cam.connected]
        logger.info(f"Connected cameras: {len(connected)}/{len(camera_manager.cameras)}")

        if len(connected) == 0:
            logger.warning("No cameras connected!")
            raise HTTPException(status_code=400, detail="No cameras connected. Connect cameras first.")

        for cam in connected:
            logger.info(f"  - {cam.serial}: enabling WiFi...")

        logger.info("=" * 60)

        results = await camera_manager.enable_wifi_all()

        logger.info("=" * 60)
        logger.info("WiFi Enable Results:")
        for serial, success in results.items():
            status = "✅ SUCCESS" if success else "❌ FAILED"
            logger.info(f"  {serial}: {status}")
        logger.info("=" * 60)

        success_count = sum(1 for s in results.values() if s)
        logger.info(f"Final: {success_count}/{len(results)} cameras have WiFi enabled")

        return {"success": True, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Enable WiFi failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wifi/disconnect")
async def disconnect_wifi():
    """Disconnect from current WiFi"""
    try:
        current = wifi_manager.get_current_wifi()
        logger.info(f"Disconnecting from WiFi: {current}")

        # Run blocking disconnect in thread pool
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            wifi_manager.disconnect
        )

        if success:
            logger.info("✅ Disconnected successfully")
            return {"success": True, "message": "Disconnected from WiFi"}
        else:
            logger.error("❌ Failed to disconnect")
            raise HTTPException(status_code=500, detail="Failed to disconnect from WiFi")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Disconnect failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Download Management ==============

@app.get("/api/media/list")
async def get_media_list():
    """Get media list from current camera"""
    try:
        media_list = download_manager.get_media_list()
        return {"media": media_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download/{serial}")
async def download_from_camera(serial: str, max_files: Optional[int] = None):
    """Download files from a camera (optionally limit to last N files)"""
    try:
        logger.info("=" * 60)
        if max_files:
            logger.info(f"📥 DOWNLOAD REQUEST for camera {serial} (last {max_files} files)")
        else:
            logger.info(f"📥 DOWNLOAD REQUEST for camera {serial} (all files)")

        camera = camera_manager.get_camera(serial)
        if not camera:
            logger.error(f"Camera {serial} not found")
            raise HTTPException(status_code=404, detail="Camera not found")

        logger.info(f"Camera Details:")
        logger.info(f"   Name: {camera.name or serial}")
        logger.info(f"   Serial: {camera.serial}")
        logger.info(f"   WiFi SSID: {camera.wifi_ssid}")
        logger.info(f"   WiFi Password: {'*' * len(camera.wifi_password)} ({len(camera.wifi_password)} chars)")
        logger.info(f"   Connected: {camera.connected}")
        logger.info("=" * 60)

        # Save current WiFi before switching
        loop = asyncio.get_event_loop()
        original_wifi = await loop.run_in_executor(None, wifi_manager.get_current_wifi)
        logger.info(f"📡 Original WiFi: {original_wifi}")

        # Connect to camera WiFi
        logger.info(f"Step 1: Connecting to camera WiFi: {camera.wifi_ssid}")
        await broadcast_message({
            "type": "download_status",
            "serial": serial,
            "status": "connecting_wifi",
            "message": f"Connecting to {camera.wifi_ssid}..."
        })

        # Run blocking WiFi connection in thread pool
        wifi_success = await loop.run_in_executor(
            None,
            wifi_manager.connect_wifi,
            camera.wifi_ssid,
            camera.wifi_password
        )

        if not wifi_success:
            error_msg = f"Failed to connect to camera WiFi: {camera.wifi_ssid}"
            logger.error(f"❌ {error_msg}")
            logger.info("=" * 60)
            await broadcast_message({
                "type": "download_error",
                "serial": serial,
                "error": error_msg
            })
            raise HTTPException(status_code=500, detail=error_msg)

        logger.info(f"✅ Successfully connected to {camera.wifi_ssid}")
        await broadcast_message({
            "type": "download_status",
            "serial": serial,
            "status": "wifi_connected",
            "message": f"Connected to {camera.wifi_ssid}, starting download..."
        })

        # Download files with progress updates
        logger.info(f"Step 2: Fetching media list from camera...")

        def progress_callback(filename: str, current: int, total: int, percent: int):
            """Progress callback that broadcasts to WebSocket"""
            logger.info(f"Downloading {current}/{total}: {filename} ({percent}%)")
            # Schedule the broadcast on the main event loop from thread
            try:
                asyncio.run_coroutine_threadsafe(
                    broadcast_message({
                        "type": "download_progress",
                        "serial": serial,
                        "filename": filename,
                        "current_file": current,
                        "total_files": total,
                        "percent": percent
                    }),
                    loop
                )
            except Exception as e:
                logger.warning(f"Could not broadcast progress: {e}")

        # Run download in thread pool
        logger.info("Step 3: Starting file download...")

        # Use partial to pass max_files parameter
        from functools import partial
        download_func = partial(
            download_manager.download_all_from_camera,
            serial,
            progress_callback,
            max_files
        )
        downloaded_files = await loop.run_in_executor(None, download_func)

        logger.info("=" * 60)
        logger.info(f"✅ Download complete!")
        logger.info(f"Downloaded {len(downloaded_files)} files from camera {serial}")
        logger.info("Files:")
        for f in downloaded_files:
            logger.info(f"  - {f.name}")
        logger.info("=" * 60)

        # Step 4: Reconnect to original WiFi for uploading
        if original_wifi and original_wifi != camera.wifi_ssid:
            logger.info(f"Step 4: Reconnecting to original WiFi: {original_wifi}")
            await broadcast_message({
                "type": "download_status",
                "serial": serial,
                "status": "reconnecting_wifi",
                "message": f"Reconnecting to {original_wifi} for internet access..."
            })

            # Disconnect from GoPro WiFi first
            await loop.run_in_executor(None, wifi_manager.disconnect)
            await asyncio.sleep(3)  # Wait for disconnect

            # Try to reconnect to original WiFi
            # Note: We don't have the password, so we rely on macOS remembering it
            logger.info(f"Attempting automatic reconnection to {original_wifi}...")
            logger.info("If this fails, you'll need to manually reconnect to your WiFi")

            await asyncio.sleep(5)  # Wait for macOS to auto-reconnect

            # Check if reconnection succeeded
            current_wifi = await loop.run_in_executor(None, wifi_manager.get_current_wifi)
            if current_wifi == original_wifi:
                logger.info(f"✅ Successfully reconnected to {original_wifi}")
                await broadcast_message({
                    "type": "download_status",
                    "serial": serial,
                    "status": "wifi_restored",
                    "message": f"Reconnected to {original_wifi}. Ready to upload!"
                })
            else:
                logger.warning(f"⚠️  Auto-reconnect failed. Current WiFi: {current_wifi}")
                logger.warning(f"Please manually reconnect to {original_wifi} to upload files")
                await broadcast_message({
                    "type": "download_status",
                    "serial": serial,
                    "status": "wifi_manual_needed",
                    "message": f"Please manually reconnect to {original_wifi} to upload files"
                })
        else:
            logger.info("Skipping WiFi reconnection (no original WiFi or same as camera WiFi)")

        await broadcast_message({
            "type": "download_complete",
            "serial": serial,
            "files_count": len(downloaded_files)
        })

        return {
            "success": True,
            "files_count": len(downloaded_files),
            "files": [str(f) for f in downloaded_files],
            "original_wifi": original_wifi
        }

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Download failed: {str(e)}"
        logger.error("=" * 60)
        logger.error(f"❌ {error_msg}")
        logger.error("=" * 60)
        logger.error("Exception details:", exc_info=True)
        await broadcast_message({
            "type": "download_error",
            "serial": serial,
            "error": error_msg
        })
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/api/downloads/list")
async def list_downloaded_files(serial: Optional[str] = None):
    """Get list of downloaded files"""
    files = download_manager.get_downloaded_files(serial)
    return {"files": files}


@app.post("/api/upload")
async def upload_file(upload: UploadModel):
    """Upload a file to S3"""
    try:
        # Check if we're on GoPro WiFi (no internet)
        current_wifi = wifi_manager.get_current_wifi()
        if current_wifi:
            # Check if current WiFi matches any camera WiFi
            for camera in camera_manager.cameras.values():
                if current_wifi == camera.wifi_ssid or camera.wifi_ssid in current_wifi:
                    logger.warning("=" * 60)
                    logger.warning(f"⚠️  WARNING: Still connected to GoPro WiFi: {current_wifi}")
                    logger.warning(f"⚠️  GoPro WiFi has no internet connectivity!")
                    logger.warning(f"⚠️  You need to disconnect and connect to your home/office WiFi")
                    logger.warning(f"⚠️  Upload will fail without internet connectivity")
                    logger.warning("=" * 60)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot upload while connected to GoPro WiFi ({current_wifi}). Please disconnect and connect to a WiFi network with internet access."
                    )

        file_path = Path(upload.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        success = await download_manager.upload_to_s3(
            file_path,
            upload.serial,
            upload.backend_url,
            upload.api_key
        )

        if success:
            return {"success": True, "message": "File uploaded successfully"}
        else:
            raise HTTPException(status_code=500, detail="Upload failed")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/create-zip")
async def create_and_upload_zip(zip_request: CreateZipModel):
    """Create ZIP of files and upload to S3"""
    try:
        logger.info("=" * 60)
        logger.info("📦 CREATE ZIP REQUEST")
        logger.info(f"Files to zip: {len(zip_request.file_paths)}")

        import zipfile
        import tempfile
        from datetime import datetime

        # Verify all files exist
        file_paths = []
        for file_path_str in zip_request.file_paths:
            file_path = Path(file_path_str)
            if not file_path.exists():
                logger.warning(f"File not found: {file_path}")
                continue
            file_paths.append(file_path)

        if not file_paths:
            raise HTTPException(status_code=404, detail="No valid files found")

        logger.info(f"Valid files: {len(file_paths)}")

        # Create ZIP filename with camera name and date
        if zip_request.zip_name:
            zip_filename = zip_request.zip_name
        else:
            # Extract camera serial from file paths
            camera_serials = set()
            for file_path in file_paths:
                # Get parent directory name (e.g., "GoPro_8881")
                parent_name = file_path.parent.name
                if parent_name.startswith("GoPro_"):
                    camera_serials.add(parent_name.replace("GoPro_", ""))

            # Create filename with camera name(s) and date
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if len(camera_serials) == 1:
                camera_name = list(camera_serials)[0]
                zip_filename = f"GoPro_{camera_name}_{timestamp}.zip"
            elif len(camera_serials) > 1:
                zip_filename = f"GoPro_MultiCam_{timestamp}.zip"
            else:
                zip_filename = f"gopro_downloads_{timestamp}.zip"

        # Create temporary ZIP file
        with tempfile.NamedTemporaryFile(mode='w+b', suffix='.zip', delete=False) as temp_zip:
            temp_zip_path = Path(temp_zip.name)

            logger.info(f"Creating ZIP: {zip_filename}")
            logger.info(f"Temp path: {temp_zip_path}")

            # Create ZIP
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in file_paths:
                    # Use relative path in ZIP (camera_serial/filename.mp4)
                    arcname = f"{file_path.parent.name}/{file_path.name}"
                    logger.info(f"  Adding: {arcname}")
                    zipf.write(file_path, arcname=arcname)

            zip_size_mb = temp_zip_path.stat().st_size / (1024 * 1024)
            logger.info(f"✓ ZIP created: {zip_size_mb:.1f} MB")

            # Upload ZIP to S3
            logger.info(f"Uploading ZIP to S3...")
            logger.info(f"Backend URL: {zip_request.backend_url}")

            # Validate backend URL
            if not zip_request.backend_url or not zip_request.backend_url.startswith('http'):
                raise ValueError(f"Invalid backend URL: {zip_request.backend_url}")

            s3_key = f"zips/{zip_filename}"
            logger.info(f"S3 Key: {s3_key}")

            import httpx

            zip_size_bytes = temp_zip_path.stat().st_size

            # Use presigned URL for large ZIPs (> 32MB)
            if zip_size_bytes > 32 * 1024 * 1024:
                logger.info(f"ZIP is > 32MB, using presigned URL method")

                # Step 1: Get presigned upload URL
                presigned_url = zip_request.backend_url.replace('/upload-file', '/upload-file-presigned')
                logger.info(f"Step 1: Getting presigned URL from {presigned_url}")

                headers = {"X-API-Key": zip_request.api_key}
                data = {
                    "filename": s3_key,
                    "content_type": "application/zip"
                }

                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(presigned_url, headers=headers, data=data)
                    resp.raise_for_status()
                    result = resp.json()

                upload_url = result["upload_url"]
                file_url = result["file_url"]
                upload_headers = result["instructions"]["headers"]
                upload_headers["x-ms-blob-type"] = "BlockBlob"  # Required for Azure

                logger.info(f"✓ Got presigned URL")
                logger.info(f"Step 2: Uploading {zip_size_mb:.1f} MB ZIP directly to Azure storage...")

                # Step 2: Upload directly to Azure blob storage
                with open(temp_zip_path, 'rb') as f:
                    file_data = f.read()

                async with httpx.AsyncClient(timeout=1200.0) as client:  # 20 min timeout for large ZIPs
                    resp = await client.put(upload_url, headers=upload_headers, content=file_data)
                    resp.raise_for_status()

                logger.info(f"✅ ZIP uploaded successfully (via presigned URL)")

                # Use the file_url from presigned response
                s3_url = file_url

            else:
                logger.info(f"ZIP is <= 32MB, using direct upload")

                with open(temp_zip_path, 'rb') as f:
                    files = {"file": (zip_filename, f, "application/zip")}
                    data = {"s3Key": s3_key}
                    headers = {"X-API-Key": zip_request.api_key}

                    logger.info(f"Sending POST request to {zip_request.backend_url}")
                    logger.info(f"Headers: X-API-Key: {zip_request.api_key[:10]}...")
                    async with httpx.AsyncClient(timeout=600.0) as client:
                        resp = await client.post(
                            zip_request.backend_url,
                            files=files,
                            data=data,
                            headers=headers
                        )
                        logger.info(f"Response status: {resp.status_code}")
                        resp.raise_for_status()
                        result = resp.json()
                        logger.info(f"Response data: {result}")

                # Extract URL from response
                s3_url = result.get("url") or result.get("fileUrl") or result.get("s3Url")
                if not s3_url:
                    s3_url = f"https://your-bucket.s3.amazonaws.com/{s3_key}"
                    logger.warning(f"Backend didn't return URL, using fallback: {s3_url}")

            # Clean up temp file
            temp_zip_path.unlink()

            logger.info("")
            logger.info("=" * 80)
            logger.info("=" * 80)
            logger.info(f"✅ ZIP UPLOAD COMPLETE!")
            logger.info("=" * 80)
            logger.info(f"📦 Filename: {zip_filename}")
            logger.info(f"📊 Size: {zip_size_mb:.1f} MB")
            logger.info(f"📁 Files: {len(file_paths)}")
            logger.info("=" * 80)
            logger.info("")
            logger.info("🔗 DOWNLOAD ZIP URL:")
            logger.info("")
            logger.info(f"   {s3_url}")
            logger.info("")
            logger.info("=" * 80)
            logger.info("=" * 80)

            return {
                "success": True,
                "zip_url": s3_url,
                "zip_filename": zip_filename,
                "zip_size_mb": round(zip_size_mb, 2),
                "files_count": len(file_paths)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ ZIP creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Health Check ==============

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "cameras_count": len(camera_manager.cameras),
        "connected_cameras": sum(1 for cam in camera_manager.cameras.values() if cam.connected)
    }


@app.post("/api/upload-camera-bulk/{serial}")
async def upload_camera_bulk(serial: str, upload_data: dict):
    """Upload all files from a specific camera as a single ZIP"""
    try:
        backend_url = upload_data.get("backend_url")
        api_key = upload_data.get("api_key")

        if not backend_url or not api_key:
            raise HTTPException(status_code=400, detail="backend_url and api_key required")

        logger.info("=" * 60)
        logger.info(f"📦 BULK UPLOAD REQUEST for camera {serial}")

        # Get all files grouped by camera
        grouped_files = download_manager.get_files_grouped_by_camera()

        # Find the folder(s) for this camera serial
        camera_folders = [folder for folder in grouped_files.keys() if serial in folder]

        if not camera_folders:
            raise HTTPException(status_code=404, detail=f"No files found for camera {serial}")

        logger.info(f"Found {len(camera_folders)} folder(s) for camera {serial}")

        upload_results = []

        # Create and upload a ZIP for each folder (date)
        for folder_name in camera_folders:
            files_info = grouped_files[folder_name]
            file_paths = [Path(f["path"]) for f in files_info]

            logger.info(f"Processing folder: {folder_name} ({len(file_paths)} files)")

            # Create ZIP
            import zipfile
            import tempfile

            zip_filename = f"{folder_name}.zip"

            with tempfile.NamedTemporaryFile(mode='w+b', suffix='.zip', delete=False) as temp_zip:
                temp_zip_path = Path(temp_zip.name)

                logger.info(f"Creating ZIP: {zip_filename}")

                with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_path in file_paths:
                        # Just use filename in ZIP (no subfolders)
                        logger.info(f"  Adding: {file_path.name}")
                        zipf.write(file_path, arcname=file_path.name)

                zip_size_mb = temp_zip_path.stat().st_size / (1024 * 1024)
                logger.info(f"✓ ZIP created: {zip_size_mb:.1f} MB")

                # Upload ZIP to S3
                logger.info(f"Uploading {zip_filename} to S3...")

                s3_key = f"zips/{zip_filename}"
                zip_size_bytes = temp_zip_path.stat().st_size

                import httpx

                # Use presigned URL for large ZIPs (> 32MB)
                if zip_size_bytes > 32 * 1024 * 1024:
                    logger.info(f"ZIP is > 32MB, using presigned URL method")

                    presigned_url = backend_url.replace('/upload-file', '/upload-file-presigned')
                    logger.info(f"Getting presigned URL from {presigned_url}")

                    headers = {"X-API-Key": api_key}
                    data = {
                        "filename": s3_key,
                        "content_type": "application/zip"
                    }

                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(presigned_url, headers=headers, data=data)
                        resp.raise_for_status()
                        result = resp.json()

                    upload_url = result["upload_url"]
                    file_url = result["file_url"]
                    upload_headers = result["instructions"]["headers"]
                    upload_headers["x-ms-blob-type"] = "BlockBlob"

                    logger.info(f"Uploading {zip_size_mb:.1f} MB directly to storage...")

                    with open(temp_zip_path, 'rb') as f:
                        file_data = f.read()

                    async with httpx.AsyncClient(timeout=1200.0) as client:
                        resp = await client.put(upload_url, headers=upload_headers, content=file_data)
                        resp.raise_for_status()

                    s3_url = file_url
                    logger.info(f"✅ Uploaded via presigned URL")

                else:
                    logger.info(f"ZIP is <= 32MB, using direct upload")

                    with open(temp_zip_path, 'rb') as f:
                        files = {"file": (zip_filename, f, "application/zip")}
                        data = {"s3Key": s3_key}
                        headers = {"X-API-Key": api_key}

                        async with httpx.AsyncClient(timeout=600.0) as client:
                            resp = await client.post(backend_url, files=files, data=data, headers=headers)
                            resp.raise_for_status()
                            result = resp.json()

                    s3_url = result.get("url") or result.get("fileUrl") or result.get("s3Url")
                    if not s3_url:
                        s3_url = f"https://storage.cloud.com/{s3_key}"

                    logger.info(f"✅ Uploaded directly")

                # Clean up temp file
                temp_zip_path.unlink()

                upload_results.append({
                    "folder": folder_name,
                    "zip_filename": zip_filename,
                    "zip_url": s3_url,
                    "zip_size_mb": round(zip_size_mb, 2),
                    "files_count": len(file_paths)
                })

                logger.info("")
                logger.info(f"✅ {zip_filename} uploaded successfully!")
                logger.info(f"📊 Size: {zip_size_mb:.1f} MB | Files: {len(file_paths)}")
                logger.info(f"🔗 URL: {s3_url}")

        logger.info("=" * 60)
        logger.info(f"✅ BULK UPLOAD COMPLETE for camera {serial}")
        logger.info(f"Total ZIPs created: {len(upload_results)}")
        logger.info("=" * 60)

        return {
            "success": True,
            "serial": serial,
            "uploads": upload_results
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Bulk upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test-s3-backend")
async def test_s3_backend(test_data: dict):
    """Test if S3 backend is reachable"""
    backend_url = test_data.get("backend_url")
    api_key = test_data.get("api_key")

    try:
        logger.info(f"Testing S3 backend: {backend_url}")

        if not backend_url or not backend_url.startswith('http'):
            return {"success": False, "error": "Invalid URL format"}

        import httpx

        # Test 1: Basic connectivity (OPTIONS request)
        async with httpx.AsyncClient(timeout=10.0) as client:
            # First try OPTIONS to see if backend is reachable
            try:
                resp = await client.options(backend_url)
                logger.info(f"OPTIONS response: {resp.status_code}")
            except Exception as e:
                logger.warning(f"OPTIONS failed (this is OK): {e}")

            # Test 2: POST with authentication header to see if auth works
            if api_key:
                headers = {"X-API-Key": api_key}
                try:
                    # Try POST without file (should fail but tells us auth is working)
                    resp = await client.post(backend_url, headers=headers, data={})
                    logger.info(f"POST test response: {resp.status_code} - {resp.text[:200]}")

                    if resp.status_code == 401 or resp.status_code == 403:
                        return {
                            "success": False,
                            "error": f"Authentication failed (API key may be invalid)",
                            "url": backend_url,
                            "status_code": resp.status_code
                        }
                    else:
                        # Any other response means backend is reachable and auth works
                        return {
                            "success": True,
                            "message": f"✅ Backend is reachable and API key is accepted (status: {resp.status_code})",
                            "url": backend_url
                        }
                except httpx.ConnectError as e:
                    return {
                        "success": False,
                        "error": f"Cannot connect to backend: {str(e)}",
                        "url": backend_url
                    }
            else:
                return {
                    "success": True,
                    "message": "Backend is reachable (no API key provided for auth test)",
                    "url": backend_url
                }

    except httpx.ConnectError as e:
        logger.error(f"Connection failed: {e}")
        return {
            "success": False,
            "error": f"Cannot connect to backend: {str(e)}",
            "url": backend_url
        }
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "url": backend_url
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
