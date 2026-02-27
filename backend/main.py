"""
FastAPI Backend for GoPro Desktop App
"""
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import asyncio
import ssl
import sys
import tempfile
import base64
import logging
from pathlib import Path

import httpx
import subprocess
import shutil
import signal
import socket
import threading

from camera_manager import CameraManager
from wifi_manager import WiFiManager
from download_manager import DownloadManager
from shoot_manager import ShootManager
from preset_manager import PresetManager
from cohn_manager import COHNManager

# Setup logging — also write to file so logs are accessible when launched from Electron
_log_file_path = Path(__file__).parent / "gopro_backend.log"
_direct_log_file = open(_log_file_path, 'w')

class _TeeHandler(logging.Handler):
    """Writes log records to our file regardless of uvicorn's logger config."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _direct_log_file.write(msg + '\n')
            _direct_log_file.flush()
        except Exception:
            pass

_tee = _TeeHandler()
_tee.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
# Attach to root so ALL loggers (main, camera_manager, open_gopro, uvicorn) get captured
logging.getLogger().addHandler(_tee)
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
shoot_manager = ShootManager()
preset_manager = PresetManager()
cohn_manager = COHNManager()
CERT_DIR = Path(tempfile.gettempdir()) / "gopro_cohn_certs"

# COHN streaming: UDP → ffmpeg (H.265→H.264 transcode) → chunked HTTP → mpegts.js in browser
import queue
_cohn_ip_to_serial: Dict[str, str] = {}  # camera IP -> serial (for UDP demux)
_cohn_stream_clients: Dict[str, List[queue.Queue]] = {}  # serial -> list of client queues
_cohn_ffmpeg_procs: Dict[str, subprocess.Popen] = {}  # serial -> ffmpeg transcoder
_cohn_reader_threads: Dict[str, threading.Thread] = {}  # serial -> stdout reader thread
_COHN_UDP_PORT = 8554
_udp_thread: Optional[threading.Thread] = None
_udp_running = False

# WebSocket connections for real-time updates
websocket_connections: List[WebSocket] = []

# Background task control
background_monitor_task = None
monitor_running = False

# Cached health data from background monitor
_cached_health_data = {}


# ============== Models ==============

class CameraModel(BaseModel):
    serial: str
    wifi_ssid: str
    wifi_password: str
    name: Optional[str] = ""


class CameraNameUpdate(BaseModel):
    name: str

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


class CreateShootModel(BaseModel):
    name: str


class SetActiveShootModel(BaseModel):
    shoot_id: str


class PresetCreateModel(BaseModel):
    name: str
    settings: dict


class PresetApplyModel(BaseModel):
    serials: Optional[List[str]] = None  # None = apply to all connected


class COHNProvisionModel(BaseModel):
    wifi_ssid: str
    wifi_password: str


class SelectedFileModel(BaseModel):
    directory: str
    filename: str


class SelectedDownloadModel(BaseModel):
    files: List[SelectedFileModel]


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
    battery_poll_counter = 0
    health_poll_counter = 0
    cohn_poll_counter = 0
    keep_alive_counter = 0
    ble_probe_counter = 0
    previous_cohn_online = {}

    # Persistent HTTP client for lightweight keep-alive pings
    keep_alive_client = httpx.AsyncClient(verify=False, timeout=2.0)

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

            # Skip all BLE polling while shutter commands are in flight
            if not camera_manager.ble_busy:
                # Poll battery every 60 seconds (120 iterations * 0.5s = 60s)
                battery_poll_counter += 1
                if battery_poll_counter >= 120:
                    battery_poll_counter = 0
                    try:
                        battery_levels = await camera_manager.get_all_battery_levels()
                        if any(v is not None for v in battery_levels.values()):
                            await broadcast_message({
                                "type": "battery_update",
                                "levels": battery_levels
                            })
                    except Exception as e:
                        logger.debug(f"Battery poll error: {e}")

                # Broadcast health data every 15 seconds (30 iterations * 0.5s = 15s)
                health_poll_counter += 1
                if health_poll_counter >= 30:
                    health_poll_counter = 0
                    try:
                        # 1) BLE health for BLE-connected cameras
                        health_data = await camera_manager.get_all_health()

                        # 2) COHN health for network-connected cameras (fills gaps BLE can't reach)
                        if cohn_manager.credentials:
                            all_creds = cohn_manager.get_all_credentials()
                            for serial, creds in all_creds.items():
                                # Skip if BLE already gave us good data (has battery + storage)
                                ble_health = health_data.get(serial, {})
                                if ble_health.get("battery_percent") is not None and ble_health.get("storage_remaining_kb") is not None:
                                    continue
                                ip = creds.get("ip_address")
                                if not ip:
                                    continue
                                auth = cohn_manager.get_auth_header(serial)
                                try:
                                    state = await _cohn_get_state(ip, auth)
                                    if "error" not in state:
                                        cam_name = ble_health.get("name") or serial
                                        cohn_health = _parse_cohn_state_to_health(serial, cam_name, state)
                                        health_data[serial] = cohn_health
                                        logger.debug(f"[{serial}] COHN health: batt={cohn_health.get('battery_percent')}% storage={cohn_health.get('storage_remaining_kb')}KB")
                                except Exception as e:
                                    logger.debug(f"[{serial}] COHN health query failed: {e}")

                        _cached_health_data.update(health_data)
                        # Log health values for debugging
                        for serial, hd in health_data.items():
                            storage = hd.get("storage_remaining_kb")
                            battery = hd.get("battery_percent")
                            if storage is not None or battery is not None:
                                src = hd.get("source", "ble")
                                logger.info(f"[{serial}] Health ({src}): battery={battery}%, storage={storage}KB")
                        if websocket_connections:
                            await broadcast_message({
                                "type": "health_update",
                                "cameras": health_data
                            })
                    except Exception as e:
                        logger.debug(f"Health poll error: {e}")

            # Dedicated keep-alive every 3 seconds (6 * 0.5s) — lightweight ping only
            keep_alive_counter += 1
            if keep_alive_counter >= 6:
                keep_alive_counter = 0
                if previous_cohn_online:
                    async def _ping_keep_alive(serial: str) -> None:
                        creds = cohn_manager.get_credentials(serial)
                        if not creds:
                            return
                        ip = creds.get("ip_address")
                        auth = cohn_manager.get_auth_header(serial)
                        try:
                            await keep_alive_client.get(
                                f"https://{ip}/gopro/camera/keep_alive",
                                headers={"Authorization": auth} if auth else {}
                            )
                        except Exception:
                            pass

                    online_serials = [s for s, ok in previous_cohn_online.items() if ok]
                    if online_serials:
                        await asyncio.gather(
                            *[_ping_keep_alive(s) for s in online_serials],
                            return_exceptions=True
                        )

            # Poll COHN cameras every 30 seconds (60 * 0.5s) - full state check + IP recovery
            cohn_poll_counter += 1
            if cohn_poll_counter >= 60:
                cohn_poll_counter = 0
                if cohn_manager.credentials:
                    try:
                        cohn_online = await cohn_manager.check_all_cameras()
                        for serial, online in cohn_online.items():
                            prev = previous_cohn_online.get(serial)
                            if prev is None or prev != online:
                                previous_cohn_online[serial] = online
                                if websocket_connections:
                                    await broadcast_message({
                                        "type": "cohn_camera_online" if online else "cohn_camera_offline",
                                        "serial": serial,
                                        "online": online
                                    })
                                # Camera came online — enforce Auto Power Down = NEVER
                                if online and prev is not True:
                                    creds = cohn_manager.get_credentials(serial)
                                    if creds:
                                        ip = creds.get("ip_address")
                                        auth = cohn_manager.get_auth_header(serial)
                                        try:
                                            resp = await keep_alive_client.get(
                                                f"https://{ip}/gopro/camera/setting?setting=59&option=0",
                                                headers={"Authorization": auth} if auth else {}
                                            )
                                            logger.info(f"[{serial}] Auto Power Down set to NEVER on reconnect: HTTP {resp.status_code}")
                                        except Exception as e:
                                            logger.debug(f"[{serial}] Failed to set Auto Power Down on reconnect: {e}")
                    except Exception as e:
                        logger.debug(f"COHN poll error: {e}")

            # Active BLE probes disabled — they send keep_alive BLE commands that
            # timeout due to response handling issues with multiple cameras sharing
            # the BLE singleton, causing false disconnections.
            # Disconnection detection relies on the passive bleak_client.is_connected
            # check in update_connection_status() above, which checks OS-level state.

            # Wait 0.5 seconds before next check (checks 2x per second)
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Connection monitor error: {e}")
            await asyncio.sleep(0.5)

    await keep_alive_client.aclose()
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

    saved_cameras_file = Path(__file__).parent.parent / "saved_cameras.json"

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

    # STEP 3: Skip auto-detect on startup (blocks event loop with BLE scanning)
    # Users can connect manually via UI buttons
    logger.info("ℹ️  Auto-detection disabled on startup — use Connect buttons in UI")


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
    success = await camera_manager.remove_camera(serial)
    if success:
        await broadcast_message({"type": "camera_removed", "serial": serial})
        return {"success": True, "message": "Camera removed"}
    else:
        raise HTTPException(status_code=404, detail="Camera not found")


@app.patch("/api/cameras/{serial}")
async def update_camera(serial: str, update: CameraNameUpdate):
    """Update a camera's display name"""
    success = camera_manager.update_camera_name(serial, update.name)
    if success:
        await broadcast_message({"type": "camera_updated", "serial": serial, "name": update.name})
        return {"success": True, "message": "Camera name updated"}
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


@app.post("/api/cameras/connect/{serial}")
async def connect_single_camera(serial: str):
    """Connect a single camera via BLE"""
    try:
        if serial not in camera_manager.cameras:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        camera = camera_manager.cameras[serial]
        logger.info(f"Connecting single camera: {serial}")
        success = await camera.connect_ble()

        await broadcast_message({
            "type": "camera_connection",
            "serial": serial,
            "connected": success
        })

        return {"success": success, "serial": serial, "connected": success}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Connect single camera failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/disconnect-all")
async def disconnect_all_cameras():
    """Disconnect all cameras"""
    try:
        await camera_manager.disconnect_all()
        return {"success": True, "message": "All cameras disconnected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/disconnect/{serial}")
async def disconnect_single_camera(serial: str):
    """Disconnect a single camera via BLE"""
    try:
        if serial not in camera_manager.cameras:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        camera = camera_manager.cameras[serial]
        logger.info(f"Disconnecting camera: {serial}")

        await camera.disconnect()

        await broadcast_message({
            "type": "camera_connection",
            "serial": serial,
            "connected": False
        })

        return {
            "success": True,
            "serial": serial,
            "connected": False
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Disconnect camera {serial} failed: {e}")
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


@app.get("/api/cameras/battery")
async def get_battery_levels():
    """Get battery levels for all cameras"""
    try:
        levels = await camera_manager.get_all_battery_levels()
        return {"levels": levels}
    except Exception as e:
        logger.error(f"Battery query failed: {e}")
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

        # Track take if a shoot is active
        successful_serials = [s for s, ok in results.items() if ok]
        take = shoot_manager.start_take(successful_serials)
        active_shoot = shoot_manager.get_active_shoot()

        response_data = {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }

        if take and active_shoot:
            response_data["take"] = take
            response_data["shoot_name"] = active_shoot["name"]
            await broadcast_message({
                "type": "take_started",
                "shoot_name": active_shoot["name"],
                "shoot_id": active_shoot["id"],
                "take": take
            })

        return response_data
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

        # Stop take if a shoot is active
        take = shoot_manager.stop_take()
        active_shoot = shoot_manager.get_active_shoot()

        response_data = {
            "success": True,
            "results": results,
            "cameras": camera_manager.list_cameras()
        }

        if take and active_shoot:
            response_data["take"] = take
            response_data["shoot_name"] = active_shoot["name"]
            await broadcast_message({
                "type": "take_stopped",
                "shoot_name": active_shoot["name"],
                "shoot_id": active_shoot["id"],
                "take": take
            })

        return response_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stop recording failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Shoot Management ==============

@app.post("/api/shoots")
async def create_shoot(shoot: CreateShootModel):
    """Create a new shoot"""
    try:
        new_shoot = shoot_manager.create_shoot(shoot.name)
        await broadcast_message({"type": "shoot_created", "shoot": new_shoot})
        return {"success": True, "shoot": new_shoot}
    except Exception as e:
        logger.error(f"Create shoot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/shoots")
async def list_shoots():
    """List all shoots"""
    return {"shoots": shoot_manager.list_shoots()}


@app.get("/api/shoots/active")
async def get_active_shoot():
    """Get the currently active shoot"""
    return {"shoot": shoot_manager.get_active_shoot()}


@app.post("/api/shoots/active")
async def set_active_shoot(body: SetActiveShootModel):
    """Set the active shoot"""
    try:
        shoot = shoot_manager.set_active_shoot(body.shoot_id)
        if not shoot:
            raise HTTPException(status_code=404, detail="Shoot not found")
        await broadcast_message({"type": "shoot_activated", "shoot": shoot})
        return {"success": True, "shoot": shoot}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Set active shoot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/shoots/deactivate")
async def deactivate_shoot():
    """End the current shoot"""
    try:
        shoot_manager.deactivate_shoot()
        await broadcast_message({"type": "shoot_deactivated"})
        return {"success": True}
    except Exception as e:
        logger.error(f"Deactivate shoot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/shoots/{shoot_id}")
async def delete_shoot(shoot_id: str):
    """Delete a shoot"""
    try:
        success = shoot_manager.delete_shoot(shoot_id)
        if not success:
            raise HTTPException(status_code=404, detail="Shoot not found")
        await broadcast_message({"type": "shoot_deleted", "shoot_id": shoot_id})
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete shoot failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Take Management ==============

class ManualTakeModel(BaseModel):
    name: Optional[str] = ""
    files: Optional[List[str]] = []

class TakeUpdateModel(BaseModel):
    name: Optional[str] = None
    files: Optional[List[str]] = None

@app.post("/api/shoots/{shoot_id}/takes")
async def create_manual_take(shoot_id: str, take_data: ManualTakeModel):
    """Create a manual take on a shoot"""
    try:
        take = shoot_manager.create_manual_take(shoot_id, take_data.name, take_data.files)
        if not take:
            raise HTTPException(status_code=404, detail="Shoot not found")
        return {"success": True, "take": take}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create manual take failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/shoots/{shoot_id}/takes/{take_number}")
async def update_take(shoot_id: str, take_number: int, updates: TakeUpdateModel):
    """Update a take's name or files"""
    try:
        update_dict = {}
        if updates.name is not None:
            update_dict["name"] = updates.name
        if updates.files is not None:
            update_dict["files"] = updates.files
        take = shoot_manager.update_take(shoot_id, take_number, update_dict)
        if not take:
            raise HTTPException(status_code=404, detail="Shoot or take not found")
        return {"success": True, "take": take}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update take failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shoots/{shoot_id}/takes/{take_number}/files")
async def get_take_files(shoot_id: str, take_number: int):
    """Get files for a specific take"""
    try:
        take = shoot_manager.get_take_files(shoot_id, take_number)
        if not take:
            raise HTTPException(status_code=404, detail="Shoot or take not found")
        return {"success": True, "take": take}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get take files failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/shoots/{shoot_id}/takes/{take_number}")
async def delete_take(shoot_id: str, take_number: int):
    """Delete a take from a shoot"""
    try:
        success = shoot_manager.delete_take(shoot_id, take_number)
        if not success:
            raise HTTPException(status_code=404, detail="Shoot or take not found")
        # Notify via WebSocket
        await broadcast({
            "type": "take_deleted",
            "shoot_id": shoot_id,
            "take_number": take_number
        })
        return {"success": True, "message": f"Take {take_number} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete take failed: {e}", exc_info=True)
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


@app.post("/api/preview/stream-start")
async def start_camera_stream():
    """Tell GoPro to start UDP/HLS stream (must be on camera WiFi)"""
    import requests as sync_requests
    try:
        logger.info("📹 Starting camera stream via HTTP...")
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: sync_requests.get("http://10.5.5.9:8080/gopro/camera/stream/start", timeout=10)
        )
        logger.info(f"Stream start response: {resp.status_code}")
        return {"success": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as e:
        logger.warning(f"Stream start failed: {e}")
        return {"success": False, "error": str(e)}


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
    """Get current WiFi status — works on macOS 26+ where SSID is hidden"""
    ssid = wifi_manager.get_current_wifi()
    ip = wifi_manager.get_current_ip()
    on_gopro = wifi_manager.is_on_gopro_network()

    # Determine network type for frontend display
    if on_gopro:
        network_type = "gopro"
        display_name = f"GoPro WiFi ({ip})"
    elif ip:
        network_type = "internet"
        display_name = ssid or f"Connected ({ip})"
    else:
        network_type = "disconnected"
        display_name = None

    return {
        "ssid": ssid,
        "ip": ip,
        "on_gopro": on_gopro,
        "network_type": network_type,
        "display_name": display_name
    }


@app.post("/api/wifi/connect")
async def connect_wifi(connection: WiFiConnectionModel):
    """Connect to a WiFi network"""
    try:
        success = wifi_manager.connect_wifi(connection.ssid, connection.password)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wifi/connect-camera/{serial}")
async def connect_camera_wifi(serial: str):
    """Connect to a camera's WiFi using server-stored credentials"""
    camera = camera_manager.get_camera(serial)
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {serial} not found")
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            wifi_manager.connect_wifi,
            camera.wifi_ssid,
            camera.wifi_password
        )
        return {"success": success, "wifi_ssid": camera.wifi_ssid}
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
async def get_media_list(serial: Optional[str] = None):
    """Get media list from camera. Uses COHN if serial provided and COHN-provisioned."""
    try:
        if serial:
            cohn = _get_cohn_params(serial)
            if cohn:
                media_list = await download_manager.async_get_media_list(
                    base_url=cohn["base_url"], auth_header=cohn["auth_header"]
                )
                return {"media": media_list, "transport": "cohn"}

        # Fallback: WiFi direct (existing sync call)
        media_list = download_manager.get_media_list()
        return {"media": media_list, "transport": "wifi_direct"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download/shoot/{shoot_id}")
async def download_all_takes_for_shoot(shoot_id: str):
    """Download ALL completed takes for a shoot from all COHN cameras in parallel.
    Fetches media list once per camera, filters files matching any take window."""
    try:
        # Find shoot
        target_shoot = None
        for s in shoot_manager.list_shoots():
            if s["id"] == shoot_id:
                target_shoot = s
                break
        if not target_shoot:
            raise HTTPException(status_code=404, detail=f"Shoot {shoot_id} not found")

        # Build take windows from completed takes
        from datetime import datetime as dt, timezone
        take_windows = []
        for take in target_shoot.get("takes", []):
            if not take.get("started_at") or not take.get("stopped_at"):
                continue
            start_naive = dt.fromisoformat(take["started_at"])
            stop_naive = dt.fromisoformat(take["stopped_at"])
            take_windows.append({
                "take_number": take["take_number"],
                "shoot_name": target_shoot["name"],
                "take_start": int(start_naive.replace(tzinfo=timezone.utc).timestamp()) - 5,
                "take_stop": int(stop_naive.replace(tzinfo=timezone.utc).timestamp()) + 5,
            })

        if not take_windows:
            raise HTTPException(status_code=400, detail="No completed takes in this shoot")

        # Get all COHN cameras
        all_creds = cohn_manager.get_all_credentials()
        if not all_creds:
            raise HTTPException(status_code=400, detail="No COHN-provisioned cameras")

        logger.info(f"Bulk download: shoot '{target_shoot['name']}', {len(take_windows)} take(s), {len(all_creds)} camera(s)")

        # Launch parallel downloads across cameras
        async def download_one_camera(serial, creds):
            ip = creds.get("ip_address")
            if not ip:
                return serial, 0, "No IP"
            base_url = f"https://{ip}"
            auth = cohn_manager.get_auth_header(serial)

            async def progress_cb(filename, current, total, percent):
                await broadcast_message({
                    "type": "download_progress",
                    "serial": serial,
                    "filename": filename,
                    "current_file": current,
                    "total_files": total,
                    "percent": percent
                })

            files = await download_manager.async_download_all_from_camera(
                serial=serial,
                base_url=base_url,
                auth_header=auth,
                progress_callback=progress_cb,
                take_windows=take_windows,
            )
            return serial, len(files), None

        tasks = [download_one_camera(s, c) for s, c in all_creds.items()]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)

        total_files = 0
        per_camera = {}
        for result in results_raw:
            if isinstance(result, Exception):
                logger.error(f"Shoot download error: {result}")
                continue
            serial, count, error = result
            per_camera[serial] = {"files": count, "error": error}
            total_files += count

            await broadcast_message({
                "type": "download_complete",
                "serial": serial,
                "files_count": count,
                "transport": "cohn"
            })

        return {
            "success": True,
            "shoot_name": target_shoot["name"],
            "takes_count": len(take_windows),
            "total_files": total_files,
            "per_camera": per_camera
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shoot download failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/download/{serial}")
async def download_from_camera(serial: str, max_files: Optional[int] = None, shoot_name: Optional[str] = None, take_number: Optional[int] = None):
    """Download files from a camera (optionally limit to last N files). COHN-first, WiFi-direct fallback."""
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

        # --- COHN path: direct HTTPS, no WiFi switching ---
        cohn = _get_cohn_params(serial)
        if cohn:
            logger.info(f"Using COHN transport: {cohn['base_url']}")
            await broadcast_message({
                "type": "download_status",
                "serial": serial,
                "status": "downloading",
                "transport": "cohn",
                "message": f"Downloading from {camera.name or serial} via COHN..."
            })

            async def cohn_progress(filename: str, current: int, total: int, percent: int):
                await broadcast_message({
                    "type": "download_progress",
                    "serial": serial,
                    "filename": filename,
                    "current_file": current,
                    "total_files": total,
                    "percent": percent
                })

            # Look up take time window for filtering
            take_start = None
            take_stop = None
            if shoot_name and take_number is not None:
                # Search all shoots for matching name
                target_shoot = None
                for s in shoot_manager.list_shoots():
                    if s["name"] == shoot_name:
                        target_shoot = s
                        break
                if target_shoot:
                    for take in target_shoot.get("takes", []):
                        if take["take_number"] == take_number:
                            if take.get("started_at") and take.get("stopped_at"):
                                from datetime import datetime as dt, timezone
                                # GoPro mod_time = local wall clock encoded as UTC
                                # So convert take times the same way (treat local as UTC)
                                start_naive = dt.fromisoformat(take["started_at"])
                                stop_naive = dt.fromisoformat(take["stopped_at"])
                                take_start = int(start_naive.replace(tzinfo=timezone.utc).timestamp()) - 5
                                take_stop = int(stop_naive.replace(tzinfo=timezone.utc).timestamp()) + 5
                                logger.info(f"Take {take_number} time window: {take['started_at']} to {take['stopped_at']} (gopro-adjusted: {take_start}-{take_stop})")
                            break

            downloaded_files = await download_manager.async_download_all_from_camera(
                serial=serial,
                base_url=cohn["base_url"],
                auth_header=cohn["auth_header"],
                progress_callback=cohn_progress,
                max_files=max_files,
                shoot_name=shoot_name,
                take_number=take_number,
                take_start=take_start,
                take_stop=take_stop
            )

            await broadcast_message({
                "type": "download_complete",
                "serial": serial,
                "files_count": len(downloaded_files),
                "transport": "cohn"
            })

            return {
                "success": True,
                "files_count": len(downloaded_files),
                "files": [str(f) for f in downloaded_files],
                "transport": "cohn"
            }

        # --- WiFi direct fallback (existing code) ---
        logger.info("No COHN credentials, falling back to WiFi direct")

        logger.info(f"Camera Details:")
        logger.info(f"   Name: {camera.name or serial}")
        logger.info(f"   Serial: {camera.serial}")
        logger.info(f"   WiFi SSID: {camera.wifi_ssid}")
        logger.info(f"   WiFi Password: {'*' * len(camera.wifi_password)} ({len(camera.wifi_password)} chars)")
        logger.info(f"   Connected: {camera.connected}")
        logger.info("=" * 60)

        # Save current network state before switching
        loop = asyncio.get_event_loop()
        original_wifi = await loop.run_in_executor(None, wifi_manager.get_current_wifi)
        original_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
        on_gopro_already = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)
        logger.info(f"📡 Original WiFi: {original_wifi or '(hidden on macOS 26)'}")
        logger.info(f"📡 Original IP: {original_ip}")
        logger.info(f"📡 Already on GoPro network: {on_gopro_already}")

        # Step 1: Enable WiFi AP on camera via BLE (must happen before Mac can connect)
        logger.info(f"Step 1: Enabling WiFi AP on camera {serial} via BLE...")
        await broadcast_message({
            "type": "download_status",
            "serial": serial,
            "status": "enabling_wifi",
            "transport": "wifi_direct",
            "message": f"Enabling WiFi on camera {serial}..."
        })

        if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
            wifi_enabled = await camera.enable_wifi()
            if not wifi_enabled:
                logger.warning(f"⚠️  WiFi AP enable returned False, attempting connection anyway...")
        else:
            logger.warning(f"⚠️  Camera {serial} not BLE-connected, attempting WiFi connection anyway...")

        # Step 2: Connect Mac to camera WiFi
        logger.info(f"Step 2: Connecting to camera WiFi: {camera.wifi_ssid}")
        await broadcast_message({
            "type": "download_status",
            "serial": serial,
            "status": "connecting_wifi",
            "transport": "wifi_direct",
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
            "transport": "wifi_direct",
            "message": f"Connected to {camera.wifi_ssid}, starting download..."
        })

        # Download files with progress updates
        logger.info(f"Step 3: Fetching media list from camera...")

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
        logger.info("Step 4: Starting file download...")

        # Use partial to pass parameters
        from functools import partial
        download_func = partial(
            download_manager.download_all_from_camera,
            serial,
            progress_callback,
            max_files,
            shoot_name=shoot_name,
            take_number=take_number
        )
        downloaded_files = await loop.run_in_executor(None, download_func)

        logger.info("=" * 60)
        logger.info(f"✅ Download complete!")
        logger.info(f"Downloaded {len(downloaded_files)} files from camera {serial}")
        logger.info("Files:")
        for f in downloaded_files:
            logger.info(f"  - {f.name}")
        logger.info("=" * 60)

        # Step 5: Reconnect to original WiFi for uploading
        should_reconnect = not on_gopro_already
        if should_reconnect:
            logger.info(f"Step 5: Reconnecting to home WiFi (original IP: {original_ip})...")
            await broadcast_message({
                "type": "download_status",
                "serial": serial,
                "status": "reconnecting_wifi",
                "transport": "wifi_direct",
                "message": "Reconnecting to home WiFi..."
            })

            await loop.run_in_executor(None, wifi_manager.disconnect)

            logger.info("Waiting for macOS to auto-reconnect to preferred network...")
            reconnected = False
            for attempt in range(10):
                await asyncio.sleep(2)
                current_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
                still_on_gopro = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)

                if current_ip and not still_on_gopro:
                    logger.info(f"✅ Reconnected to home WiFi (IP: {current_ip})")
                    await broadcast_message({
                        "type": "download_status",
                        "serial": serial,
                        "status": "wifi_restored",
                        "transport": "wifi_direct",
                        "message": f"Reconnected to home WiFi! Ready to upload."
                    })
                    reconnected = True
                    break
                logger.info(f"   Waiting... attempt {attempt+1}/10 (IP: {current_ip})")

            if not reconnected:
                logger.warning("⚠️  Auto-reconnect to home WiFi timed out")
                logger.warning("Please manually reconnect to your WiFi to upload files")
                await broadcast_message({
                    "type": "download_status",
                    "serial": serial,
                    "status": "wifi_manual_needed",
                    "transport": "wifi_direct",
                    "message": "Please manually reconnect to your home WiFi to upload files"
                })
        else:
            logger.info("Skipping WiFi reconnection (was already on GoPro network before download)")

        await broadcast_message({
            "type": "download_complete",
            "serial": serial,
            "files_count": len(downloaded_files),
            "transport": "wifi_direct"
        })

        return {
            "success": True,
            "files_count": len(downloaded_files),
            "files": [str(f) for f in downloaded_files],
            "original_wifi": original_wifi,
            "transport": "wifi_direct"
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


@app.post("/api/download/{serial}/latest")
async def download_latest_from_camera(serial: str, shoot_name: Optional[str] = None, take_number: Optional[int] = None):
    """Download only the latest video from a camera. COHN-first, WiFi-direct fallback."""
    try:
        logger.info("=" * 60)
        logger.info(f"DOWNLOAD LATEST VIDEO REQUEST for camera {serial}")

        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        # --- COHN path ---
        cohn = _get_cohn_params(serial)
        if cohn:
            logger.info(f"Using COHN transport: {cohn['base_url']}")
            await broadcast_message({
                "type": "download_status", "serial": serial,
                "status": "downloading", "transport": "cohn",
                "message": f"Downloading latest video from {camera.name or serial} via COHN..."
            })

            async def cohn_progress(filename, current, total, percent):
                await broadcast_message({
                    "type": "download_progress", "serial": serial,
                    "filename": filename, "current_file": current,
                    "total_files": total, "percent": percent
                })

            downloaded_files = await download_manager.async_download_latest_from_camera(
                serial=serial, base_url=cohn["base_url"], auth_header=cohn["auth_header"],
                progress_callback=cohn_progress, shoot_name=shoot_name, take_number=take_number
            )

            await broadcast_message({
                "type": "download_complete", "serial": serial,
                "files_count": len(downloaded_files), "transport": "cohn"
            })
            return {"success": True, "files_count": len(downloaded_files),
                    "files": [str(f) for f in downloaded_files], "transport": "cohn"}

        # --- WiFi direct fallback ---
        logger.info("No COHN credentials, falling back to WiFi direct")
        loop = asyncio.get_event_loop()
        on_gopro_already = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)

        # Enable WiFi AP on camera via BLE
        if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
            await camera.enable_wifi()

        # Connect to camera WiFi
        await broadcast_message({
            "type": "download_status", "serial": serial,
            "status": "connecting_wifi", "transport": "wifi_direct",
            "message": f"Connecting to {camera.wifi_ssid}..."
        })

        wifi_success = await loop.run_in_executor(
            None, wifi_manager.connect_wifi, camera.wifi_ssid, camera.wifi_password
        )

        if not wifi_success:
            error_msg = f"Failed to connect to camera WiFi: {camera.wifi_ssid}"
            await broadcast_message({"type": "download_error", "serial": serial, "error": error_msg})
            raise HTTPException(status_code=500, detail=error_msg)

        await broadcast_message({
            "type": "download_status", "serial": serial,
            "status": "wifi_connected", "transport": "wifi_direct",
            "message": f"Connected to {camera.wifi_ssid}, downloading latest video..."
        })

        def progress_callback(filename: str, current: int, total: int, percent: int):
            try:
                asyncio.run_coroutine_threadsafe(
                    broadcast_message({
                        "type": "download_progress", "serial": serial,
                        "filename": filename, "current_file": current,
                        "total_files": total, "percent": percent
                    }),
                    loop
                )
            except Exception as e:
                logger.warning(f"Could not broadcast progress: {e}")

        from functools import partial
        download_func = partial(
            download_manager.download_latest_from_camera,
            serial, progress_callback,
            shoot_name=shoot_name, take_number=take_number
        )
        downloaded_files = await loop.run_in_executor(None, download_func)

        # Reconnect to home WiFi
        if not on_gopro_already:
            await loop.run_in_executor(None, wifi_manager.disconnect)
            for attempt in range(10):
                await asyncio.sleep(2)
                current_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
                still_on_gopro = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)
                if current_ip and not still_on_gopro:
                    break

        await broadcast_message({
            "type": "download_complete", "serial": serial,
            "files_count": len(downloaded_files), "transport": "wifi_direct"
        })

        return {"success": True, "files_count": len(downloaded_files),
                "files": [str(f) for f in downloaded_files], "transport": "wifi_direct"}

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Download latest failed: {str(e)}"
        logger.error(f"{error_msg}", exc_info=True)
        await broadcast_message({"type": "download_error", "serial": serial, "error": error_msg})
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/api/download/{serial}/selected")
async def download_selected_from_camera(serial: str, selection: SelectedDownloadModel):
    """Download selected files from a camera. COHN-first, WiFi-direct fallback."""
    try:
        logger.info("=" * 60)
        logger.info(f"SELECTIVE DOWNLOAD REQUEST for camera {serial}: {len(selection.files)} files")

        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        file_list = [{"directory": f.directory, "filename": f.filename} for f in selection.files]

        # --- COHN path ---
        cohn = _get_cohn_params(serial)
        if cohn:
            logger.info(f"Using COHN transport: {cohn['base_url']}")
            await broadcast_message({
                "type": "download_status", "serial": serial,
                "status": "downloading", "transport": "cohn",
                "message": f"Downloading {len(file_list)} selected file(s) via COHN..."
            })

            async def cohn_progress(filename, current, total, percent):
                await broadcast_message({
                    "type": "download_progress", "serial": serial,
                    "filename": filename, "current_file": current,
                    "total_files": total, "percent": percent
                })

            downloaded_files = await download_manager.async_download_selected_from_camera(
                serial=serial, base_url=cohn["base_url"], auth_header=cohn["auth_header"],
                file_list=file_list, progress_callback=cohn_progress
            )

            await broadcast_message({
                "type": "download_complete", "serial": serial,
                "files_count": len(downloaded_files), "transport": "cohn"
            })
            return {"success": True, "files_count": len(downloaded_files),
                    "files": [str(f) for f in downloaded_files], "transport": "cohn"}

        # --- WiFi direct fallback ---
        logger.info("No COHN credentials, falling back to WiFi direct")
        loop = asyncio.get_event_loop()
        on_gopro_already = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)

        # Enable WiFi AP on camera via BLE
        if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
            await camera.enable_wifi()

        # Connect to camera WiFi
        await broadcast_message({
            "type": "download_status", "serial": serial,
            "status": "connecting_wifi", "transport": "wifi_direct",
            "message": f"Connecting to {camera.wifi_ssid}..."
        })

        wifi_success = await loop.run_in_executor(
            None, wifi_manager.connect_wifi, camera.wifi_ssid, camera.wifi_password
        )

        if not wifi_success:
            error_msg = f"Failed to connect to camera WiFi: {camera.wifi_ssid}"
            await broadcast_message({"type": "download_error", "serial": serial, "error": error_msg})
            raise HTTPException(status_code=500, detail=error_msg)

        await broadcast_message({
            "type": "download_status", "serial": serial,
            "status": "wifi_connected", "transport": "wifi_direct",
            "message": f"Connected. Downloading {len(selection.files)} selected file(s)..."
        })

        def progress_callback(filename: str, current: int, total: int, percent: int):
            try:
                asyncio.run_coroutine_threadsafe(
                    broadcast_message({
                        "type": "download_progress", "serial": serial,
                        "filename": filename, "current_file": current,
                        "total_files": total, "percent": percent
                    }),
                    loop
                )
            except Exception as e:
                logger.warning(f"Could not broadcast progress: {e}")

        downloaded_files = await loop.run_in_executor(
            None,
            download_manager.download_selected_from_camera,
            serial, file_list, progress_callback
        )

        # Reconnect to home WiFi
        if not on_gopro_already:
            await loop.run_in_executor(None, wifi_manager.disconnect)
            for attempt in range(10):
                await asyncio.sleep(2)
                current_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
                still_on_gopro = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)
                if current_ip and not still_on_gopro:
                    break

        await broadcast_message({
            "type": "download_complete", "serial": serial,
            "files_count": len(downloaded_files), "transport": "wifi_direct"
        })

        return {"success": True, "files_count": len(downloaded_files),
                "files": [str(f) for f in downloaded_files], "transport": "wifi_direct"}

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Selected download failed: {str(e)}"
        logger.error(f"{error_msg}", exc_info=True)
        await broadcast_message({"type": "download_error", "serial": serial, "error": error_msg})
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/api/browse/{serial}")
async def browse_camera(serial: str):
    """Browse media files on a camera SD card. COHN-first, WiFi-direct fallback."""
    try:
        logger.info("=" * 60)
        logger.info(f"BROWSE REQUEST for camera {serial}")

        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        # --- COHN path: instant, no WiFi switching ---
        cohn = _get_cohn_params(serial)
        if cohn:
            logger.info(f"Browsing via COHN: {cohn['base_url']}")
            await broadcast_message({
                "type": "browse_status", "serial": serial,
                "status": "scanning", "transport": "cohn",
                "message": "Scanning camera media via COHN..."
            })

            summary = await download_manager.async_get_media_summary(
                base_url=cohn["base_url"], auth_header=cohn["auth_header"]
            )

            logger.info(f"Found {summary['total_files']} files ({summary['total_size_human']}) via COHN")

            await broadcast_message({
                "type": "browse_complete", "serial": serial,
                "summary": summary, "transport": "cohn"
            })
            return {"success": True, "serial": serial, "summary": summary, "transport": "cohn"}

        # --- WiFi direct fallback ---
        logger.info("No COHN credentials, falling back to WiFi direct")
        loop = asyncio.get_event_loop()
        on_gopro_already = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)

        # Enable WiFi AP on camera via BLE
        if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
            await camera.enable_wifi()

        await broadcast_message({
            "type": "browse_status", "serial": serial,
            "status": "connecting_wifi", "transport": "wifi_direct",
            "message": f"Connecting to {camera.wifi_ssid}..."
        })

        wifi_success = await loop.run_in_executor(
            None, wifi_manager.connect_wifi, camera.wifi_ssid, camera.wifi_password
        )

        if not wifi_success:
            error_msg = f"Failed to connect to camera WiFi: {camera.wifi_ssid}"
            await broadcast_message({"type": "browse_status", "serial": serial, "status": "error", "message": error_msg})
            raise HTTPException(status_code=500, detail=error_msg)

        await broadcast_message({
            "type": "browse_status", "serial": serial,
            "status": "scanning", "transport": "wifi_direct",
            "message": "Scanning camera media..."
        })

        summary = await loop.run_in_executor(None, download_manager.get_media_summary)

        logger.info(f"Found {summary['total_files']} files ({summary['total_size_human']})")

        # Reconnect to home WiFi
        if not on_gopro_already:
            await broadcast_message({
                "type": "browse_status", "serial": serial,
                "status": "reconnecting_wifi", "transport": "wifi_direct",
                "message": "Reconnecting to home WiFi..."
            })
            await loop.run_in_executor(None, wifi_manager.disconnect)
            for attempt in range(10):
                await asyncio.sleep(2)
                current_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
                still_on_gopro = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)
                if current_ip and not still_on_gopro:
                    break

        await broadcast_message({
            "type": "browse_complete", "serial": serial,
            "summary": summary, "transport": "wifi_direct"
        })

        return {"success": True, "serial": serial, "summary": summary, "transport": "wifi_direct"}

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Browse failed: {str(e)}"
        logger.error(f"{error_msg}", exc_info=True)
        await broadcast_message({"type": "browse_status", "serial": serial, "status": "error", "message": error_msg})
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/api/cameras/{serial}/media-summary")
async def get_media_summary(serial: str):
    """Get summary of media on camera SD card. COHN-first, WiFi-direct fallback."""
    try:
        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        logger.info(f"Getting media summary for camera {serial}")

        # --- COHN path ---
        cohn = _get_cohn_params(serial)
        if cohn:
            logger.info(f"Media summary via COHN: {cohn['base_url']}")
            summary = await download_manager.async_get_media_summary(
                base_url=cohn["base_url"], auth_header=cohn["auth_header"]
            )
            logger.info(f"Media summary for {serial}: {summary['total_files']} files, {summary['total_size_human']}")
            return summary

        # --- WiFi direct fallback ---
        if not camera.connected:
            raise HTTPException(status_code=400, detail=f"Camera {serial} is not connected. Connect via BLE first.")

        if camera.recording:
            raise HTTPException(status_code=400, detail=f"Camera {serial} is currently recording. Stop recording before erasing.")

        logger.info("No COHN credentials, falling back to WiFi direct")

        # Enable WiFi AP on camera via BLE
        if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
            await camera.enable_wifi()

        # Connect to camera WiFi
        loop = asyncio.get_event_loop()
        wifi_success = await loop.run_in_executor(
            None,
            wifi_manager.connect_wifi,
            camera.wifi_ssid,
            camera.wifi_password
        )

        if not wifi_success:
            raise HTTPException(status_code=500, detail=f"Failed to connect to camera WiFi: {camera.wifi_ssid}")

        # Get media summary
        summary = await loop.run_in_executor(None, download_manager.get_media_summary)

        logger.info(f"Media summary for {serial}: {summary['total_files']} files, {summary['total_size_human']}")

        return summary

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Media summary failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cameras/{serial}/erase-sd")
async def erase_sd_card(serial: str):
    """Erase all media from camera SD card. Uses COHN if available, falls back to WiFi direct."""
    try:
        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")

        if camera.recording:
            raise HTTPException(status_code=400, detail=f"Camera {serial} is currently recording. Stop recording before erasing.")

        logger.info("=" * 60)
        logger.info(f"🗑️  ERASE SD CARD REQUEST for camera {serial}")
        logger.info("=" * 60)

        # Try COHN first (no WiFi switching needed)
        creds = cohn_manager.get_credentials(serial)
        if creds and creds.get("ip_address"):
            ip = creds["ip_address"]
            username = creds.get("username", "")
            password = creds.get("password", "")
            auth_token = base64.b64encode(f"{username}:{password}".encode()).decode()
            auth_header = f"Basic {auth_token}"

            logger.info(f"Erasing via COHN: https://{ip}/gp/gpControl/command/storage/delete/all")
            try:
                resp = await _cohn_http_get(ip, auth_header, "/gp/gpControl/command/storage/delete/all", timeout=60.0)
                success = resp.status_code == 200
                if success:
                    logger.info(f"✅ Successfully erased all media from camera {serial} via COHN")
                    # Give camera time to process the deletion
                    await asyncio.sleep(3)
                else:
                    logger.error(f"❌ COHN erase failed: HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"COHN erase request failed: {e}")
                success = False
        else:
            # Fall back to WiFi direct
            if not camera.connected:
                raise HTTPException(status_code=400, detail=f"Camera {serial} is not connected and has no COHN credentials.")

            loop = asyncio.get_event_loop()
            on_gopro_already = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)

            if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
                await camera.enable_wifi()

            wifi_success = await loop.run_in_executor(
                None, wifi_manager.connect_wifi, camera.wifi_ssid, camera.wifi_password
            )
            if not wifi_success:
                raise HTTPException(status_code=500, detail=f"Failed to connect to camera WiFi: {camera.wifi_ssid}")

            success = await loop.run_in_executor(None, download_manager.erase_all_media)

            if success:
                logger.info(f"✅ Successfully erased all media from camera {serial} via WiFi direct")
            else:
                logger.error(f"❌ Failed to erase media from camera {serial}")

            if not on_gopro_already:
                logger.info("Reconnecting to home WiFi...")
                await loop.run_in_executor(None, wifi_manager.disconnect)
                for attempt in range(10):
                    await asyncio.sleep(2)
                    current_ip = await loop.run_in_executor(None, wifi_manager.get_current_ip)
                    still_on_gopro = await loop.run_in_executor(None, wifi_manager.is_on_gopro_network)
                    if current_ip and not still_on_gopro:
                        logger.info(f"✅ Reconnected to home WiFi (IP: {current_ip})")
                        break

        # Broadcast WebSocket message
        await broadcast_message({
            "type": "sd_erased",
            "serial": serial,
            "success": success
        })

        if not success:
            raise HTTPException(status_code=500, detail="Failed to erase SD card")

        return {"success": True, "message": f"All media erased from camera {serial}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erase SD failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/downloads/list")
async def list_downloaded_files(serial: Optional[str] = None):
    """Get list of downloaded files"""
    files = download_manager.get_downloaded_files(serial)
    return {"files": files}


@app.post("/api/upload")
async def upload_file(upload: UploadModel):
    """Upload a file to S3"""
    try:
        # Check if we're on GoPro WiFi (no internet) — use IP-based detection for macOS 26+
        if wifi_manager.is_on_gopro_network():
            current_wifi = wifi_manager.get_current_wifi() or "GoPro WiFi"
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

            # Upload ZIP to S3 via shared helper
            s3_key = f"zips/{zip_filename}"
            logger.info(f"Uploading ZIP to S3 (key: {s3_key})...")

            s3_url = await download_manager.upload_file_to_backend(
                temp_zip_path, s3_key,
                zip_request.backend_url, zip_request.api_key,
                content_type="application/zip"
            )
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


# ============== Camera Health Dashboard ==============

def _parse_cohn_state_to_health(serial: str, name: str, state: dict) -> dict:
    """Convert raw COHN /gopro/camera/state response into health dict"""
    status = state.get("status", {})
    # GoPro status IDs (string keys in JSON):
    # "2"=encoding, "6"=system_hot, "8"=is_busy, "10"=gps_stat,
    # "13"=video_progress(sec), "33"=sd_status, "35"=video_rem(min),
    # "54"=space_rem(KB), "70"=int_batt_per, "86"=thermal_mitigation
    health = {
        "serial": serial,
        "name": name,
        "connected": True,
        "source": "cohn",
    }
    batt = status.get("70")
    health["battery_percent"] = int(batt) if batt is not None else None
    health["battery_drain_rate"] = None

    space = status.get("54")
    health["storage_remaining_kb"] = int(space) if space is not None else None
    vrem = status.get("35")
    health["video_remaining_min"] = int(vrem) if vrem is not None else None
    sd = status.get("33")
    health["sd_status"] = str(sd) if sd is not None else None

    enc = status.get("8") or status.get("2")
    health["is_encoding"] = bool(enc) if enc is not None else False
    health["recording"] = health["is_encoding"]
    vprog = status.get("13")
    health["recording_duration_sec"] = int(vprog) if vprog is not None else None

    hot = status.get("6")
    health["system_hot"] = bool(hot) if hot is not None else False
    health["too_cold"] = False
    therm = status.get("86")
    health["thermal_mitigation"] = bool(therm) if therm is not None else False

    gps = status.get("10")
    health["gps_lock"] = bool(gps) if gps is not None else False

    health["num_videos"] = status.get("39")
    health["num_photos"] = status.get("40")
    health["orientation"] = None

    return health


@app.get("/api/health/dashboard")
async def get_health_dashboard():
    """Get health status — serves cached data from background monitor (instant, non-blocking)"""
    try:
        health_data = {}

        # Clear stale cached entries for cameras no longer in the manager
        active_serials = set(camera_manager.cameras.keys())
        stale_keys = [k for k in _cached_health_data if k not in active_serials]
        for k in stale_keys:
            del _cached_health_data[k]

        # Return cached health data (populated by background monitor every 15s)
        for serial, camera in camera_manager.cameras.items():
            if serial in _cached_health_data:
                cached = _cached_health_data[serial]
                # Update connection status from live camera state
                cached["connected"] = camera.connected
                cached["recording"] = cached.get("recording") or camera.recording
                health_data[serial] = cached
            else:
                # No cached data yet — return complete field set with defaults
                health_data[serial] = {
                    "serial": serial,
                    "name": camera.name,
                    "connected": camera.connected,
                    "recording": camera.recording,
                    "battery_percent": camera.battery_level,
                    "battery_drain_rate": None,
                    "storage_remaining_kb": None,
                    "video_remaining_min": None,
                    "sd_status": None,
                    "is_encoding": camera.recording,
                    "recording_duration_sec": None,
                    "system_hot": False,
                    "too_cold": False,
                    "thermal_mitigation": False,
                    "gps_lock": False,
                    "num_videos": None,
                    "num_photos": None,
                    "orientation": None,
                    "source": None,
                }

        return {"cameras": health_data}
    except Exception as e:
        logger.error(f"Health dashboard failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health/{serial}")
async def get_camera_health(serial: str):
    """Get detailed health for a single camera"""
    try:
        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")
        health = await camera.get_health_status()
        return health
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Camera health failed for {serial}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Preset Management ==============

@app.get("/api/presets")
async def list_presets():
    """List all saved presets"""
    return {"presets": preset_manager.list_presets()}


@app.post("/api/presets")
async def create_preset(preset: PresetCreateModel):
    """Create or update a preset"""
    try:
        saved = preset_manager.save_preset(preset.name, preset.settings)
        return {"success": True, "preset": {preset.name: saved}}
    except Exception as e:
        logger.error(f"Create preset failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/presets/capture/{serial}")
async def capture_preset(serial: str, body: dict):
    """Capture current settings from a camera as a new preset.
    Uses BLE first, then enriches with COHN state to fill missing settings (e.g. shutter)."""
    try:
        camera = camera_manager.get_camera(serial)
        if not camera:
            raise HTTPException(status_code=404, detail=f"Camera {serial} not found")
        if not camera.connected:
            raise HTTPException(status_code=400, detail=f"Camera {serial} not connected")

        name = body.get("name", f"Captured from {camera.name}")
        settings = await camera.get_current_settings()
        if "error" in settings:
            raise HTTPException(status_code=500, detail=settings["error"])

        # Enrich with COHN state if available (fills in settings BLE doesn't read, like shutter)
        cohn = _get_cohn_params(serial)
        if cohn:
            try:
                state = await _cohn_get_state(cohn["base_url"].replace("https://", ""), cohn["auth_header"])
                if "error" not in state:
                    # Build reverse lookup: setting_id -> (setting_name, {option_id -> value_str})
                    reverse_ids = {v: k for k, v in GOPRO_SETTING_IDS.items()}
                    cohn_settings = state.get("settings", {})
                    for sid_str, option_val in cohn_settings.items():
                        try:
                            sid = int(sid_str)
                        except (ValueError, TypeError):
                            continue
                        setting_name = reverse_ids.get(sid)
                        if not setting_name or setting_name in settings:
                            continue
                        # Reverse-lookup option value to friendly name
                        values_map = GOPRO_SETTING_VALUES.get(setting_name, {})
                        reverse_values = {v: k for k, v in values_map.items()}
                        friendly = reverse_values.get(option_val, str(option_val))
                        settings[setting_name] = friendly
                        logger.info(f"[{serial}] COHN enriched: {setting_name} = {friendly}")
            except Exception as e:
                logger.debug(f"[{serial}] COHN enrichment failed: {e}")

        saved = preset_manager.save_preset(name, settings)
        return {"success": True, "name": name, "preset": saved}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Capture preset failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/presets/{name}/apply")
async def apply_preset(name: str, body: PresetApplyModel):
    """Apply a preset to one or more cameras"""
    try:
        preset = preset_manager.get_preset(name)
        if not preset:
            raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")

        # Extract only the setting keys (exclude metadata like created_at)
        setting_keys = {"resolution", "fps", "video_fov", "hypersmooth", "anti_flicker", "shutter"}
        settings = {k: v for k, v in preset.items() if k in setting_keys}

        # Determine target cameras
        if body.serials:
            targets = [(s, camera_manager.get_camera(s)) for s in body.serials]
            targets = [(s, c) for s, c in targets if c and c.connected]
        else:
            targets = [(s, c) for s, c in camera_manager.cameras.items() if c.connected]

        if not targets:
            raise HTTPException(status_code=400, detail="No connected cameras to apply preset to")

        results = {}
        for serial, camera in targets:
            try:
                result = await camera.apply_settings(settings)
                results[serial] = result
            except Exception as e:
                results[serial] = {"error": str(e)}

        return {"success": True, "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Apply preset failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/presets/{name}")
async def delete_preset(name: str):
    """Delete a preset"""
    success = preset_manager.delete_preset(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")
    return {"success": True, "message": f"Preset '{name}' deleted"}


@app.patch("/api/presets/{name}")
async def toggle_preset_pin(name: str):
    """Toggle pinned status on a preset"""
    new_pinned = preset_manager.toggle_pin(name)
    if new_pinned is None:
        raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")
    return {"success": True, "name": name, "pinned": new_pinned}


# ============== COHN Settings (HTTP-based, works over network) ==============

# GoPro setting ID → name mapping, and name → value → option ID mapping
GOPRO_SETTING_IDS = {
    "resolution": 2,
    "fps": 3,
    "video_fov": 121,    # Video Digital Lens
    "hypersmooth": 135,
    "anti_flicker": 134,
    "gps": 83,
    "shutter": 145,      # Shutter Speed
}

# Maps friendly enum names to GoPro numeric option values
GOPRO_SETTING_VALUES = {
    "resolution": {
        "RES_4K": 1, "RES_2_7K": 4, "RES_2_7K_4_3": 6, "RES_1080": 9,
        "RES_4K_4_3": 18, "RES_5_3K": 100, "RES_5_3K_4_3": 107,
        "RES_4K_9_16": 111, "RES_1080_9_16": 112,
        # Friendly aliases
        "4K": 1, "2.7K": 4, "2.7K 4:3": 6, "1080": 9, "1080p": 9,
        "4K 4:3": 18, "5.3K": 100, "5.3K 4:3": 107,
    },
    "fps": {
        "FPS_240": 0, "FPS_120": 1, "FPS_100": 2, "FPS_60": 5,
        "FPS_50": 6, "FPS_30": 8, "FPS_25": 9, "FPS_24": 10, "FPS_200": 13,
        # Friendly aliases
        "240": 0, "120": 1, "100": 2, "60": 5, "50": 6,
        "30": 8, "25": 9, "24": 10, "200": 13,
    },
    "video_fov": {
        "WIDE": 0, "LINEAR": 4, "NARROW": 2, "SUPERVIEW": 3,
        "LINEAR_HORIZON_LEVELING": 8, "HYPERVIEW": 9, "LINEAR_HORIZON_LOCK": 10,
        "Wide": 0, "Linear": 4, "Narrow": 2, "SuperView": 3,
    },
    "hypersmooth": {
        "OFF": 0, "ON": 1, "HIGH": 2, "BOOST": 3, "AUTO_BOOST": 4, "STANDARD": 100,
        "Off": 0, "On": 1, "High": 2, "Boost": 3, "AutoBoost": 4,
    },
    "anti_flicker": {
        "NTSC": 0, "PAL": 1,
        "60HZ": 0, "50HZ": 1,
        "60Hz": 0, "50Hz": 1,
    },
    "gps": {
        "OFF": 0, "ON": 1,
        "Off": 0, "On": 1, "0": 0, "1": 1,
    },
    "shutter": {
        "AUTO": 0, "1/30": 5, "1/60": 8, "1/120": 13, "1/240": 18,
        "1/480": 22, "1/960": 23, "1/48": 6, "1/50": 7, "1/96": 11,
        "1/100": 12, "1/192": 15, "1/200": 16, "1/360": 19, "1/400": 20,
        # Friendly aliases
        "auto": 0, "Auto": 0,
    },
}


def _get_cohn_params(serial: str) -> Optional[dict]:
    """Return COHN base_url + auth_header if camera is COHN-provisioned, else None."""
    creds = cohn_manager.get_credentials(serial)
    if not creds or not creds.get("ip_address"):
        return None
    return {
        "base_url": f"https://{creds['ip_address']}",
        "auth_header": cohn_manager.get_auth_header(serial)
    }


async def _cohn_http_get(ip: str, auth_header: str, path: str, timeout: float = 10.0) -> httpx.Response:
    """Make an authenticated HTTPS GET to a COHN camera"""
    headers = {"Authorization": auth_header} if auth_header else {}
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        return await client.get(f"https://{ip}{path}", headers=headers)


async def _cohn_set_setting(ip: str, auth_header: str, setting_name: str, value_str: str) -> dict:
    """Set a camera setting via COHN HTTPS"""
    setting_id = GOPRO_SETTING_IDS.get(setting_name)
    if setting_id is None:
        return {"error": f"Unknown setting: {setting_name}"}

    values_map = GOPRO_SETTING_VALUES.get(setting_name, {})
    option = values_map.get(value_str)
    if option is None:
        try:
            option = int(value_str)
        except ValueError:
            return {"error": f"Unknown value '{value_str}' for {setting_name}"}

    try:
        resp = await _cohn_http_get(ip, auth_header,
            f"/gopro/camera/setting?setting={setting_id}&option={option}")
        if resp.status_code == 200:
            return {"success": True, "setting": setting_name, "value": value_str}
        else:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def _cohn_get_state(ip: str, auth_header: str) -> dict:
    """Get camera state via COHN HTTPS"""
    try:
        resp = await _cohn_http_get(ip, auth_header, "/gopro/camera/state")
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@app.post("/api/cohn/settings/apply")
async def cohn_apply_settings(body: dict):
    """Apply settings to all COHN cameras via HTTPS (no BLE needed)"""
    settings = body.get("settings", {})
    serials = body.get("serials")  # Optional: specific cameras, else all provisioned

    all_creds = cohn_manager.get_all_credentials()
    if serials:
        targets = {s: all_creds[s] for s in serials if s in all_creds}
    else:
        targets = all_creds

    if not targets:
        raise HTTPException(status_code=400, detail="No provisioned cameras")

    results = {}
    for serial, creds in targets.items():
        ip = creds.get("ip_address")
        auth = cohn_manager.get_auth_header(serial)
        if not ip:
            results[serial] = {"error": "No IP"}
            continue

        cam_results = {}
        for setting_name, value_str in settings.items():
            cam_results[setting_name] = await _cohn_set_setting(ip, auth, setting_name, str(value_str))
        results[serial] = cam_results

    return {"results": results}


@app.post("/api/cohn/gps/enable")
async def cohn_enable_gps():
    """Enable GPS on all COHN cameras"""
    all_creds = cohn_manager.get_all_credentials()
    results = {}
    for serial, creds in all_creds.items():
        ip = creds.get("ip_address")
        auth = cohn_manager.get_auth_header(serial)
        if ip:
            results[serial] = await _cohn_set_setting(ip, auth, "gps", "ON")
        else:
            results[serial] = {"error": "No IP"}
    return {"results": results}


@app.get("/api/cohn/camera/state/{serial}")
async def cohn_get_camera_state(serial: str):
    """Get full camera state via COHN HTTPS"""
    creds = cohn_manager.get_credentials(serial)
    if not creds:
        raise HTTPException(status_code=404, detail="Camera not provisioned")
    ip = creds.get("ip_address")
    auth = cohn_manager.get_auth_header(serial)
    return await _cohn_get_state(ip, auth)


@app.post("/api/presets/{name}/apply-cohn")
async def apply_preset_cohn(name: str, body: dict = {}):
    """Apply a preset to COHN cameras via HTTPS (no BLE needed)"""
    preset = preset_manager.get_preset(name)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")

    setting_keys = {"resolution", "fps", "video_fov", "hypersmooth", "anti_flicker", "shutter"}
    settings = {k: v for k, v in preset.items() if k in setting_keys and v is not None}

    serials = body.get("serials")
    all_creds = cohn_manager.get_all_credentials()
    targets = {s: all_creds[s] for s in serials if s in all_creds} if serials else all_creds

    if not targets:
        raise HTTPException(status_code=400, detail="No provisioned cameras")

    results = {}
    for serial, creds in targets.items():
        ip = creds.get("ip_address")
        auth = cohn_manager.get_auth_header(serial)
        if not ip:
            results[serial] = {"error": "No IP"}
            continue
        cam_results = {}
        for setting_name, value_str in settings.items():
            cam_results[setting_name] = await _cohn_set_setting(ip, auth, setting_name, str(value_str))
        results[serial] = cam_results

    return {"success": True, "results": results}


# ============== COHN (Camera on Home Network) ==============

def _get_cohn_ssl_context(creds: dict, serial: str):
    """Build SSL context for GoPro COHN cameras (self-signed certs, skip verification)"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _udp_listener_thread():
    """Separate thread: receives UDP on port 8554 and fans out raw MPEG-TS packets
    to connected browser clients via per-camera queues. No ffmpeg needed."""
    global _udp_running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("0.0.0.0", _COHN_UDP_PORT))
    sock.settimeout(1.0)
    logger.info(f"[COHN UDP] Listener thread started on port {_COHN_UDP_PORT}")
    pkt_count = 0
    while _udp_running:
        try:
            data, addr = sock.recvfrom(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        src_ip = addr[0]
        pkt_count += 1
        if pkt_count <= 6:
            logger.info(f"[COHN UDP] Packet #{pkt_count} from {addr}, size={len(data)}, mapped={_cohn_ip_to_serial.get(src_ip, 'UNKNOWN')}")
        serial = _cohn_ip_to_serial.get(src_ip)
        if not serial:
            continue
        # Write to ffmpeg transcoder stdin (H.265 → H.264)
        proc = _cohn_ffmpeg_procs.get(serial)
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
    sock.close()
    logger.info("[COHN UDP] Listener thread stopped")


def _ensure_udp_thread():
    """Start the UDP listener thread if not already running."""
    global _udp_thread, _udp_running
    if _udp_thread is not None and _udp_thread.is_alive():
        return True
    _udp_running = True
    _udp_thread = threading.Thread(target=_udp_listener_thread, daemon=True)
    _udp_thread.start()
    return True


def _stop_udp_thread():
    """Stop the UDP listener thread."""
    global _udp_thread, _udp_running
    _udp_running = False
    if _udp_thread is not None:
        _udp_thread.join(timeout=3)
        _udp_thread = None


def _ffmpeg_reader_thread(serial: str, proc: subprocess.Popen):
    """Read transcoded H.264 MPEG-TS from ffmpeg stdout and fan out to browser clients."""
    logger.info(f"[COHN {serial}] Reader thread started")
    while True:
        try:
            data = proc.stdout.read(32768)
        except (OSError, ValueError):
            break
        if not data:
            break
        for q in list(_cohn_stream_clients.get(serial, [])):
            try:
                q.put_nowait(data)
            except queue.Full:
                # Drain stale data to make room (drop oldest to keep stream fresh)
                dropped = 0
                while dropped < 50:
                    try:
                        q.get_nowait()
                        dropped += 1
                    except queue.Empty:
                        break
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass
    logger.info(f"[COHN {serial}] Reader thread stopped")


def _start_transcoder(serial: str) -> bool:
    """Start ffmpeg transcoder: reads H.265 MPEG-TS from stdin, outputs H.264 MPEG-TS to stdout."""
    _stop_transcoder(serial)

    if serial not in _cohn_stream_clients:
        _cohn_stream_clients[serial] = []

    ffmpeg_bin = shutil.which("ffmpeg") or ("ffmpeg.exe" if sys.platform == "win32" else "/opt/homebrew/bin/ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-f", "mpegts",
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "2M",
        "-maxrate", "2.5M",
        "-bufsize", "4M",
        "-g", "15",
        "-an",
        "-f", "mpegts",
        "pipe:1"
    ]

    logger.info(f"[COHN {serial}] Starting H.265→H.264 transcoder")
    try:
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **popen_kwargs
        )
        _cohn_ffmpeg_procs[serial] = proc

        reader = threading.Thread(
            target=_ffmpeg_reader_thread, args=(serial, proc), daemon=True
        )
        _cohn_reader_threads[serial] = reader
        reader.start()
        return True
    except Exception as e:
        logger.error(f"[COHN {serial}] Failed to start transcoder: {e}")
        return False


def _stop_transcoder(serial: str):
    """Stop ffmpeg transcoder and reader thread for a camera."""
    proc = _cohn_ffmpeg_procs.pop(serial, None)
    if proc and proc.poll() is None:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info(f"[COHN {serial}] Stopped transcoder")

    # Reader thread will exit when proc.stdout closes
    _cohn_reader_threads.pop(serial, None)

    # Signal browser clients to disconnect
    clients = _cohn_stream_clients.pop(serial, [])
    for q in clients:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


async def _start_single_cohn_preview(serial: str, creds: dict) -> dict:
    """Start preview stream on a COHN camera via HTTPS + UDP-to-HLS relay"""
    ip = creds.get("ip_address")
    if not ip:
        return {"success": False, "error": "No IP address"}

    ssl_ctx = _get_cohn_ssl_context(creds, serial)
    auth_header = cohn_manager.get_auth_header(serial)
    headers = {"Authorization": auth_header} if auth_header else {}

    try:
        # Register IP→serial mapping for UDP demuxer thread
        _cohn_ip_to_serial[ip] = serial
        _start_transcoder(serial)

        # Ensure UDP listener thread is running
        _ensure_udp_thread()

        # Use webcam API to start streaming (sends TS over UDP to our IP:8554)
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            # First ensure clean state
            for cleanup_path in ["/gopro/webcam/stop", "/gopro/webcam/exit"]:
                try:
                    await client.get(f"https://{ip}{cleanup_path}", headers=headers)
                except Exception:
                    pass
            await asyncio.sleep(1)

            # Start webcam mode (sends to port 8554)
            resp = await client.get(
                f"https://{ip}/gopro/webcam/start?port={_COHN_UDP_PORT}",
                headers=headers
            )
            logger.info(f"[COHN {serial}] webcam/start: {resp.status_code} {resp.text}")
            if resp.status_code != 200:
                _stop_transcoder(serial)
                return {"success": False, "error": f"webcam/start HTTP {resp.status_code}"}

            # Start preview (this triggers UDP streaming)
            resp = await client.get(
                f"https://{ip}/gopro/webcam/preview",
                headers=headers
            )
            logger.info(f"[COHN {serial}] webcam/preview: {resp.status_code} {resp.text}")
            if resp.status_code != 200:
                _stop_transcoder(serial)
                return {"success": False, "error": f"webcam/preview HTTP {resp.status_code}"}

        # Direct MPEG-TS stream URL (no HLS, no ffmpeg — raw TS via chunked HTTP)
        stream_url = f"http://127.0.0.1:8000/api/cohn/stream/{serial}"
        return {"success": True, "stream_url": stream_url, "ip": ip}
    except Exception as e:
        _stop_transcoder(serial)
        return {"success": False, "error": str(e)}


async def _stop_single_cohn_preview(serial: str, creds: dict) -> dict:
    """Stop preview stream on a COHN camera via HTTPS"""
    ip = creds.get("ip_address")
    if not ip:
        return {"success": False, "error": "No IP address"}

    ssl_ctx = _get_cohn_ssl_context(creds, serial)
    auth_header = cohn_manager.get_auth_header(serial)
    headers = {"Authorization": auth_header} if auth_header else {}

    # Unregister stream and remove IP mapping
    _stop_transcoder(serial)
    _cohn_ip_to_serial.pop(ip, None)

    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            # Stop webcam preview and exit webcam mode
            for path in ["/gopro/webcam/stop", "/gopro/webcam/exit"]:
                try:
                    await client.get(f"https://{ip}{path}", headers=headers)
                except Exception:
                    pass
            return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _reenable_cohn_via_ble(serial: str) -> dict:
    """Re-enable COHN on a camera using the SDK's BLE connection.
    This is needed when cameras wake from sleep and lose their COHN WiFi connection."""
    cam = camera_manager.cameras.get(serial)
    if not cam or not cam.connected or not cam.gopro:
        return {"success": False, "error": "Camera not connected via BLE"}

    try:
        gopro = cam.gopro
        # COHN enable command: Feature 0xF1, Action 0x65, Protobuf field 1 = True
        # Protobuf encoding: field_tag(1, varint) = 0x08, value = 0x01
        protobuf_data = bytes([0x08, 0x01])
        payload = bytearray([len(protobuf_data) + 2, 0xF1, 0x65]) + bytearray(protobuf_data)

        # Write directly to the BLE command characteristic (fire-and-forget)
        from open_gopro.ble import BleUUID
        cq_command_uuid = BleUUID("CQ_COMMAND", hex="b5f90072-aa8d-11e3-9046-0002a5d5c51b")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, gopro._ble.write, cq_command_uuid, payload)
        logger.info(f"[COHN {serial}] COHN enable command sent via BLE")
        return {"success": True}
    except Exception as e:
        logger.error(f"[COHN {serial}] Re-enable failed: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/cohn/reenable")
async def reenable_cohn_all():
    """Re-enable COHN on all provisioned cameras via BLE"""
    all_creds = cohn_manager.get_all_credentials()
    results = {}
    for serial in all_creds:
        result = await _reenable_cohn_via_ble(serial)
        results[serial] = result
    # Wait for cameras to connect to WiFi
    await asyncio.sleep(5)
    # Check which are now online
    online_status = await cohn_manager.check_all_cameras()
    for serial in results:
        results[serial]["online"] = online_status.get(serial, False)
    return {"results": results}


@app.post("/api/cohn/reenable/{serial}")
async def reenable_cohn_single(serial: str):
    """Re-enable COHN on a single camera via BLE"""
    result = await _reenable_cohn_via_ble(serial)
    if result.get("success"):
        await asyncio.sleep(5)
        online = await cohn_manager.check_camera_online(serial)
        result["online"] = online
    return result


@app.get("/api/cohn/networks")
async def get_cohn_networks():
    """List all saved WiFi networks and their camera counts"""
    networks = cohn_manager.get_all_networks()
    return {
        "active_ssid": cohn_manager.wifi_ssid,
        "networks": networks
    }


@app.post("/api/cohn/networks/switch")
async def switch_cohn_network(body: dict):
    """Switch to a different WiFi network's stored credentials"""
    wifi_ssid = body.get("wifi_ssid")
    if not wifi_ssid:
        raise HTTPException(status_code=400, detail="wifi_ssid is required")

    wifi_password = body.get("wifi_password")

    # Require password for new networks
    networks = cohn_manager.get_all_networks()
    if wifi_ssid not in networks and not wifi_password:
        raise HTTPException(status_code=400, detail="wifi_password is required for new networks")

    cohn_manager.switch_network(wifi_ssid, wifi_password)

    # Return fresh status for the switched network
    all_creds = cohn_manager.get_all_credentials()
    online_status = await cohn_manager.check_all_cameras()
    cameras = {}
    for serial, creds in all_creds.items():
        cameras[serial] = {
            "provisioned": True,
            "ip_address": creds.get("ip_address"),
            "username": creds.get("username"),
            "mac_address": creds.get("mac_address"),
            "provisioned_at": creds.get("provisioned_at"),
            "online": online_status.get(serial, False)
        }

    return {
        "success": True,
        "active_ssid": cohn_manager.wifi_ssid,
        "cameras": cameras
    }


@app.patch("/api/cohn/camera/{serial}/ip")
async def update_camera_ip(serial: str, body: dict):
    """Manually update a camera's stored IP address (fallback when ARP can't find it)"""
    ip_address = body.get("ip_address")
    if not ip_address:
        raise HTTPException(status_code=400, detail="ip_address is required")
    if not cohn_manager.is_provisioned(serial):
        raise HTTPException(status_code=404, detail=f"Camera {serial} not provisioned")
    success = cohn_manager.update_ip(serial, ip_address)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update IP")
    # Verify connectivity at new IP
    online = await cohn_manager.check_camera_online(serial)
    return {"success": True, "serial": serial, "ip_address": ip_address, "online": online}


@app.get("/api/cohn/status")
async def get_cohn_status():
    """Get COHN status for all cameras"""
    all_creds = cohn_manager.get_all_credentials()
    online_status = await cohn_manager.check_all_cameras()

    result = {}
    for serial, creds in all_creds.items():
        result[serial] = {
            "provisioned": True,
            "ip_address": creds.get("ip_address"),
            "username": creds.get("username"),
            "mac_address": creds.get("mac_address"),
            "provisioned_at": creds.get("provisioned_at"),
            "online": online_status.get(serial, False)
        }

    # Include non-provisioned cameras
    for serial in camera_manager.cameras:
        if serial not in result:
            result[serial] = {"provisioned": False, "online": False}

    return {"cameras": result, "active_ssid": cohn_manager.wifi_ssid}


@app.get("/api/cohn/status/{serial}")
async def get_cohn_status_single(serial: str):
    """Get COHN status for a single camera"""
    creds = cohn_manager.get_credentials(serial)
    if not creds:
        return {"serial": serial, "provisioned": False, "online": False}

    online = await cohn_manager.check_camera_online(serial)
    return {
        "serial": serial,
        "provisioned": True,
        "ip_address": creds.get("ip_address"),
        "username": creds.get("username"),
        "mac_address": creds.get("mac_address"),
        "provisioned_at": creds.get("provisioned_at"),
        "online": online
    }


@app.post("/api/cohn/provision/{serial}")
async def provision_cohn(serial: str, body: COHNProvisionModel):
    """Provision a camera for COHN via BLE"""
    try:
        # Disconnect SDK BLE first if connected
        camera = camera_manager.get_camera(serial)
        if camera and camera.connected:
            logger.info(f"Disconnecting SDK BLE for {serial} before COHN provisioning...")
            try:
                await camera.disconnect()
            except Exception as e:
                logger.warning(f"SDK BLE disconnect warning: {e}")

        async def progress_callback(step, total, msg):
            await broadcast_message({
                "type": "cohn_provisioning_progress",
                "serial": serial,
                "step": step,
                "total": total,
                "message": msg
            })

        result = await cohn_manager.provision_camera(
            serial, body.wifi_ssid, body.wifi_password, progress_callback
        )

        await broadcast_message({
            "type": "cohn_provisioning_complete",
            "serial": serial,
            "ip_address": result.get("ip_address"),
            "success": True
        })

        return result

    except Exception as e:
        logger.error(f"COHN provisioning failed for {serial}: {e}", exc_info=True)
        await broadcast_message({
            "type": "cohn_provisioning_error",
            "serial": serial,
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/cohn/provision/{serial}")
async def remove_cohn_provision(serial: str):
    """Remove COHN credentials for a camera"""
    success = cohn_manager.remove_credentials(serial)
    if success:
        return {"success": True, "message": f"COHN credentials removed for {serial}"}
    else:
        raise HTTPException(status_code=404, detail=f"No COHN credentials found for {serial}")


@app.get("/api/cohn/stream/{serial}")
async def stream_cohn_ts(serial: str):
    """Stream raw MPEG-TS directly to browser via chunked HTTP.
    mpegts.js in the frontend decodes this with sub-second latency."""
    if serial not in _cohn_stream_clients:
        raise HTTPException(status_code=404, detail="Camera not streaming")

    client_queue: queue.Queue = queue.Queue(maxsize=10000)
    _cohn_stream_clients[serial].append(client_queue)
    logger.info(f"[COHN {serial}] Browser client connected to stream")

    def _blocking_get():
        """Block in thread pool until data arrives (frees event loop)."""
        return client_queue.get(timeout=2.0)

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                # Wait for first chunk in a thread (doesn't block event loop)
                try:
                    first = await loop.run_in_executor(None, _blocking_get)
                except queue.Empty:
                    continue  # timeout, retry
                if first is None:
                    return  # stream stopped

                # Batch any additional ready chunks
                chunks = [first]
                try:
                    while len(chunks) < 100:
                        chunks.append(client_queue.get_nowait())
                except queue.Empty:
                    pass
                yield b"".join(chunks)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _cohn_stream_clients.get(serial, []).remove(client_queue)
            except (ValueError, KeyError):
                pass
            logger.info(f"[COHN {serial}] Browser client disconnected from stream")

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        generate(),
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
        }
    )


_SNAPSHOT_BASE_PORT = 9100  # Snapshot captures use ports 9100+ (separate from stream port 8554)


def _udp_to_pipe_thread(sock: socket.socket, proc_stdin, stop_event: threading.Event):
    """Thread: read UDP packets from sock and write to ffmpeg stdin until stop or ffmpeg exits."""
    try:
        while not stop_event.is_set():
            try:
                data, _ = sock.recvfrom(65536)
                if data:
                    proc_stdin.write(data)
                    proc_stdin.flush()
            except socket.timeout:
                continue
            except (BrokenPipeError, OSError):
                break
    except Exception:
        pass
    finally:
        try:
            proc_stdin.close()
        except Exception:
            pass


async def _capture_single_snapshot_inner(serial: str, ip: str, auth: str, port: int, name: str) -> dict:
    """Capture a single JPEG frame from a COHN camera via webcam/UDP/ffmpeg.
    Uses a Python UDP socket to receive the stream and pipes it to ffmpeg stdin,
    because ffmpeg 8.0's built-in UDP demuxer has issues receiving on macOS."""
    headers = {"Authorization": auth} if auth else {}
    ffmpeg_bin = shutil.which("ffmpeg") or ("ffmpeg.exe" if sys.platform == "win32" else "/opt/homebrew/bin/ffmpeg")
    proc = None
    udp_sock = None
    stop_event = threading.Event()
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            # Clean up any existing webcam state
            for cleanup_path in ["/gopro/webcam/stop", "/gopro/webcam/exit"]:
                try:
                    await client.get(f"https://{ip}{cleanup_path}", headers=headers)
                except Exception:
                    pass
            await asyncio.sleep(0.5)

            # Bind our own UDP socket BEFORE telling the camera to stream
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 512 * 1024)
            udp_sock.settimeout(1.0)
            udp_sock.bind(("0.0.0.0", port))

            # Start webcam on dedicated snapshot port
            resp = await client.get(
                f"https://{ip}/gopro/webcam/start?port={port}", headers=headers
            )
            if resp.status_code != 200:
                udp_sock.close()
                return {"serial": serial, "name": name, "error": f"webcam/start HTTP {resp.status_code}"}

            # Start ffmpeg reading from stdin pipe
            popen_kwargs = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-y",
                "-fflags", "nobuffer+discardcorrupt+genpts",
                "-analyzeduration", "10000000",
                "-probesize", "5000000",
                "-f", "mpegts",
                "-i", "pipe:0",
                "-frames:v", "1",
                "-f", "image2",
                "-vcodec", "mjpeg",
                "-q:v", "2",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"[COHN {serial}] ffmpeg+UDP proxy on port {port}")

            # Start UDP→pipe thread
            pipe_thread = threading.Thread(
                target=_udp_to_pipe_thread,
                args=(udp_sock, proc.stdin, stop_event),
                daemon=True,
            )
            pipe_thread.start()

            # Trigger the stream
            resp = await client.get(
                f"https://{ip}/gopro/webcam/preview", headers=headers
            )
            if resp.status_code != 200:
                stop_event.set()
                proc.kill()
                udp_sock.close()
                await client.get(f"https://{ip}/gopro/webcam/stop", headers=headers)
                return {"serial": serial, "name": name, "error": f"webcam/preview HTTP {resp.status_code}"}

            logger.info(f"[COHN {serial}] webcam/preview triggered, waiting for frame...")

            # Wait for ffmpeg to capture one frame
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=12)
            except asyncio.TimeoutError:
                proc.kill()
                stdout = b""
                stderr = b""
                logger.warning(f"[COHN {serial}] ffmpeg timed out waiting for frame")

            # Signal thread to stop and clean up
            stop_event.set()
            udp_sock.close()
            udp_sock = None

            # Stop webcam
            for cleanup_path in ["/gopro/webcam/stop", "/gopro/webcam/exit"]:
                try:
                    await client.get(f"https://{ip}{cleanup_path}", headers=headers)
                except Exception:
                    pass

            if stdout and len(stdout) > 500:
                logger.info(f"[COHN {serial}] Snapshot captured: {len(stdout)} bytes")
                return {
                    "serial": serial,
                    "name": name,
                    "dataUrl": f"data:image/jpeg;base64,{base64.b64encode(stdout).decode()}",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                }
            stderr_text = stderr.decode(errors='ignore')[-200:] if stderr else "no stderr"
            logger.warning(f"[COHN {serial}] No frame captured. ffmpeg stderr: {stderr_text}")
            return {"serial": serial, "name": name, "error": "No frame captured"}
    except Exception as e:
        logger.error(f"[COHN {serial}] Snapshot error: {e}")
        stop_event.set()
        if proc and proc.returncode is None:
            proc.kill()
        return {"serial": serial, "name": name, "error": str(e)}
    finally:
        if udp_sock:
            try:
                udp_sock.close()
            except Exception:
                pass


async def _capture_single_snapshot(serial: str, ip: str, auth: str, port: int, name: str) -> dict:
    """Wrapper with one retry: on error, cleanup webcam state, wait 2s, try again."""
    result = await _capture_single_snapshot_inner(serial, ip, auth, port, name)
    if "error" not in result:
        return result

    logger.info(f"[COHN {serial}] Snapshot failed ({result.get('error')}), retrying once...")
    # Cleanup webcam state before retry
    headers = {"Authorization": auth} if auth else {}
    try:
        async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
            for path in ["/gopro/webcam/stop", "/gopro/webcam/exit"]:
                try:
                    await client.get(f"https://{ip}{path}", headers=headers)
                except Exception:
                    pass
    except Exception:
        pass
    await asyncio.sleep(2)
    return await _capture_single_snapshot_inner(serial, ip, auth, port, name)


@app.post("/api/cohn/snapshot/all")
async def cohn_snapshot_all():
    """Capture a single JPEG frame from each online COHN camera (no live preview needed).
    Uses check_all_cameras() which includes IP recovery via ARP when DHCP IPs change."""
    all_creds = cohn_manager.get_all_credentials()
    if not all_creds:
        raise HTTPException(status_code=400, detail="No COHN-provisioned cameras")

    # Use check_all_cameras() which includes ARP-based IP recovery
    online_status = await cohn_manager.check_all_cameras()

    tasks = []
    errors = {}
    for idx, (serial, creds) in enumerate(all_creds.items()):
        if not online_status.get(serial, False):
            errors[serial] = "Camera offline (unreachable after IP recovery attempt)"
            continue
        # Re-read creds after check_all_cameras (IP may have been updated by recovery)
        creds = cohn_manager.get_credentials(serial) or creds
        ip = creds.get("ip_address")
        if not ip:
            errors[serial] = "No IP address"
            continue
        auth = cohn_manager.get_auth_header(serial)
        cam_name = serial
        cam = camera_manager.get_camera(serial)
        if cam:
            cam_name = cam.name or cam_name
        port = _SNAPSHOT_BASE_PORT + idx
        tasks.append(_capture_single_snapshot(serial, ip, auth, port, cam_name))

    if not tasks:
        return {"snapshots": {}, "errors": errors, "error": "No online COHN cameras found"}

    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    snapshots = {}
    for result in results_list:
        if isinstance(result, dict) and "serial" in result:
            snapshots[result["serial"]] = result
            if "error" in result:
                errors[result["serial"]] = result["error"]
        elif isinstance(result, Exception):
            logger.error(f"Snapshot task failed: {result}")

    return {"snapshots": snapshots, "errors": errors}


@app.post("/api/cohn/preview/start")
async def start_cohn_preview_all():
    """Start preview on all COHN-provisioned cameras"""
    all_creds = cohn_manager.get_all_credentials()
    if not all_creds:
        raise HTTPException(status_code=400, detail="No COHN-provisioned cameras")

    results = {}
    tasks = []
    serials = []

    for serial, creds in all_creds.items():
        tasks.append(_start_single_cohn_preview(serial, creds))
        serials.append(serial)

    task_results = await asyncio.gather(*tasks, return_exceptions=True)
    for serial, result in zip(serials, task_results):
        if isinstance(result, Exception):
            results[serial] = {"success": False, "error": str(result)}
        else:
            results[serial] = result

        await broadcast_message({
            "type": "cohn_preview_started",
            "serial": serial,
            "success": results[serial].get("success", False),
            "stream_url": results[serial].get("stream_url")
        })

    return {"results": results}


@app.post("/api/cohn/preview/start/{serial}")
async def start_cohn_preview_single(serial: str):
    """Start preview on a single COHN camera"""
    creds = cohn_manager.get_credentials(serial)
    if not creds:
        raise HTTPException(status_code=404, detail=f"No COHN credentials for {serial}")

    result = await _start_single_cohn_preview(serial, creds)

    await broadcast_message({
        "type": "cohn_preview_started",
        "serial": serial,
        "success": result.get("success", False),
        "stream_url": result.get("stream_url")
    })

    return result


@app.post("/api/cohn/preview/stop")
async def stop_cohn_preview_all():
    """Stop preview on all COHN-provisioned cameras"""
    all_creds = cohn_manager.get_all_credentials()
    if not all_creds:
        raise HTTPException(status_code=400, detail="No COHN-provisioned cameras")

    results = {}
    tasks = []
    serials = []

    for serial, creds in all_creds.items():
        tasks.append(_stop_single_cohn_preview(serial, creds))
        serials.append(serial)

    task_results = await asyncio.gather(*tasks, return_exceptions=True)
    for serial, result in zip(serials, task_results):
        if isinstance(result, Exception):
            results[serial] = {"success": False, "error": str(result)}
        else:
            results[serial] = result

        await broadcast_message({
            "type": "cohn_preview_stopped",
            "serial": serial,
            "success": results[serial].get("success", False)
        })

    return {"results": results}


@app.post("/api/cohn/preview/stop/{serial}")
async def stop_cohn_preview_single(serial: str):
    """Stop preview on a single COHN camera"""
    creds = cohn_manager.get_credentials(serial)
    if not creds:
        raise HTTPException(status_code=404, detail=f"No COHN credentials for {serial}")

    result = await _stop_single_cohn_preview(serial, creds)

    await broadcast_message({
        "type": "cohn_preview_stopped",
        "serial": serial,
        "success": result.get("success", False)
    })

    return result


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

                # Upload ZIP to S3 via shared helper
                s3_key = f"zips/{zip_filename}"
                logger.info(f"Uploading {zip_filename} to S3 (key: {s3_key})...")

                s3_url = await download_manager.upload_file_to_backend(
                    temp_zip_path, s3_key,
                    backend_url, api_key,
                    content_type="application/zip"
                )
                if not s3_url:
                    s3_url = f"https://storage.cloud.com/{s3_key}"

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
