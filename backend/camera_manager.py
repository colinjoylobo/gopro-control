"""
GoPro Camera Management via BLE
"""
import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from open_gopro import GoPro, Params
from open_gopro.constants import StatusId, SettingId
import logging

logger = logging.getLogger(__name__)

# CRITICAL FIX: BleakWrapperController is a singleton but __init__ runs on every GoPro()
# creation, spawning a new event loop thread each time and orphaning the old one.
# This causes "Future attached to a different loop" for all cameras except the last one.
# Patch it to only initialize once.
try:
    from open_gopro.ble.adapters.bleak_wrapper import BleakWrapperController
    _original_bleak_init = BleakWrapperController.__init__

    def _patched_bleak_init(self, *args, **kwargs):
        if hasattr(self, '_patched_initialized') and self._patched_initialized:
            return
        _original_bleak_init(self, *args, **kwargs)
        self._patched_initialized = True

    BleakWrapperController.__init__ = _patched_bleak_init
    logger.info("Patched BleakWrapperController singleton __init__")
except Exception as e:
    logger.warning(f"Failed to patch BleakWrapperController: {e}")

# CRITICAL FIX: _find_device() hardcodes retries=30 (= 150 seconds of scanning for an offline camera).
# open() calls _find_device() without forwarding its own retries parameter.
# Patch to use retries=3 (= 15 seconds max) so offline cameras fail fast and don't block the BLE singleton.
try:
    from open_gopro.ble.client import BleClient
    _original_find_device = BleClient._find_device

    def _patched_find_device(self, timeout=5, retries=3):
        return _original_find_device(self, timeout=timeout, retries=retries)

    BleClient._find_device = _patched_find_device
    logger.info("Patched BleClient._find_device to use retries=3 (was 30)")
except Exception as e:
    logger.warning(f"Failed to patch BleClient._find_device: {e}")


class CameraInstance:
    def __init__(self, serial: str, wifi_ssid: str, wifi_password: str, name: str = ""):
        self.serial = serial
        self.wifi_ssid = wifi_ssid
        self.wifi_password = wifi_password
        self.name = name or f"GoPro {serial}"
        self.gopro: Optional[GoPro] = None
        self.connected = False
        self.recording = False
        self.battery_level: Optional[int] = None
        self.recording_start_time: Optional[datetime] = None
        self.battery_history: list = []  # [(timestamp, percent), ...]

    async def connect_ble(self) -> bool:
        """Connect via BLE only, with retry"""
        if self.connected and self.gopro and self.gopro.is_ble_connected:
            logger.info(f"[{self.serial}] Already connected, skipping reconnection")
            return True

        last4 = self.serial[-4:] if len(self.serial) > 4 else self.serial
        ble_target = f"GoPro {last4}"
        loop = asyncio.get_event_loop()

        for attempt in range(2):
            try:
                logger.info(f"[{self.serial}] Connecting BLE (attempt {attempt+1}, target: {ble_target})...")
                self.gopro = GoPro(target=ble_target, enable_wifi=False)

                await asyncio.wait_for(
                    loop.run_in_executor(None, self.gopro.open),
                    timeout=25
                )

                logger.info(f"[{self.serial}] Connection opened, disabling internal state machine...")

                # Disable open_gopro's internal _maintain_ble state machine.
                # Its _ready lock blocks all BLE commands when the _maintain_state
                # thread doesn't process status notifications in time (common with
                # multiple cameras sharing the BLE singleton). We manage state ourselves.
                self.gopro._maintain_ble = False

                try:
                    self.gopro.ble_command.enable_wifi_ap(False)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"[{self.serial}] WiFi disable warning (ignoring): {e}")

                self.connected = True
                logger.info(f"[{self.serial}] BLE connected successfully")
                return True

            except asyncio.TimeoutError:
                logger.error(f"[{self.serial}] BLE connection timeout (attempt {attempt+1})")
                if self.gopro:
                    try:
                        await loop.run_in_executor(None, self.gopro.close)
                    except Exception:
                        pass
                    self.gopro = None
                if attempt == 0:
                    logger.info(f"[{self.serial}] Retrying in 3 seconds...")
                    await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"[{self.serial}] BLE connection failed (attempt {attempt+1}): {type(e).__name__}: {e}")
                if self.gopro:
                    try:
                        await loop.run_in_executor(None, self.gopro.close)
                    except Exception:
                        pass
                    self.gopro = None
                if attempt == 0:
                    logger.info(f"[{self.serial}] Retrying in 3 seconds...")
                    await asyncio.sleep(3)

        self.connected = False
        return False

    async def disconnect(self):
        """Disconnect BLE"""
        if self.gopro:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.gopro.close)
                logger.info(f"[{self.serial}] Disconnected")
            except Exception as e:
                logger.error(f"[{self.serial}] Disconnect error: {e}")
            # Always clean up state, even if close() failed (stale handle)
            self.connected = False
            self.recording = False

    def _ble_cmd_in_thread(self, func, args=(), timeout=15):
        """Run a BLE command in a dedicated thread (not executor pool).
        Executor threads have stale asyncio loops that cause 'attached to different loop' errors.
        Returns (result, error) tuple."""
        result = [None]
        error = [None]

        def target():
            try:
                result[0] = func(*args)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            return None, "timeout"
        if error[0]:
            return result[0], error[0]
        return result[0], None

    def _fire_shutter_raw(self, shutter: "Params.Shutter") -> bool:
        """Fire shutter via raw BLE write — no response wait.

        Writes directly to the BLE command characteristic, bypassing open_gopro's
        response handling which blocks for 5+ seconds per camera.  This is
        fire-and-forget: the camera starts/stops recording on receipt of the
        BLE write regardless of whether we read the acknowledgement.
        """
        try:
            from open_gopro.constants import GoProUUIDs
            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot fire shutter: Not connected")
                return False

            is_start = (shutter == Params.Shutter.ON)
            action = "start" if is_start else "stop"

            # Raw shutter command bytes:
            # [length=3, cmd=SET_SHUTTER(0x01), param_len=1, value]
            value = 0x01 if is_start else 0x00
            data = bytearray([0x03, 0x01, 0x01, value])

            self.gopro._ble.write(GoProUUIDs.CQ_COMMAND, data)

            self.recording = is_start
            logger.info(f"[{self.serial}] Shutter {action} sent (raw BLE write)")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] Shutter fire failed: {e}")
            return False

    async def start_recording(self) -> bool:
        """Start recording with retry."""
        try:
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot record: Not connected")
                return False

            for attempt in range(2):
                try:
                    logger.info(f"[{self.serial}] Starting recording (attempt {attempt+1})...")
                    # Timer unblocks SDK's encoding_started.wait() so thread finishes cleanly
                    self.gopro._encoding_started.clear()
                    timer = threading.Timer(5.0, self.gopro._encoding_started.set)
                    timer.daemon = True
                    timer.start()

                    loop = asyncio.get_event_loop()
                    _, err = await loop.run_in_executor(
                        None,
                        self._ble_cmd_in_thread,
                        self.gopro.ble_command.set_shutter,
                        (Params.Shutter.ON,),
                        20
                    )
                    timer.cancel()

                    if err == "timeout":
                        # BLE write likely succeeded but encoding_started wasn't set
                        logger.info(f"[{self.serial}] Shutter sent (encoding wait timed out — likely recording)")
                        self.recording = True
                        self.recording_start_time = datetime.now()
                        return True
                    elif err:
                        raise err

                    self.recording = True
                    self.recording_start_time = datetime.now()
                    logger.info(f"[{self.serial}] Recording started")
                    return True
                except Exception as e:
                    logger.warning(f"[{self.serial}] Recording attempt {attempt+1} failed: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2)

            logger.error(f"[{self.serial}] Start recording failed after retries")
            return False

        except Exception as e:
            logger.error(f"[{self.serial}] Start recording failed: {e}")
            return False

    async def stop_recording(self) -> bool:
        """Stop recording with retry"""
        try:
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot stop recording: Not connected")
                return False

            for attempt in range(2):
                try:
                    logger.info(f"[{self.serial}] Stopping recording (attempt {attempt+1})...")
                    # Unblock any lingering start thread
                    self.gopro._encoding_started.set()
                    await asyncio.sleep(0.5)

                    loop = asyncio.get_event_loop()
                    _, err = await loop.run_in_executor(
                        None,
                        self._ble_cmd_in_thread,
                        self.gopro.ble_command.set_shutter,
                        (Params.Shutter.OFF,),
                        10
                    )
                    if err and err != "timeout":
                        raise err
                    await asyncio.sleep(2)
                    self.recording = False
                    self.recording_start_time = None
                    logger.info(f"[{self.serial}] Recording stopped")
                    return True
                except Exception as e:
                    logger.warning(f"[{self.serial}] Stop attempt {attempt+1} failed: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2)

            # Even if command failed, mark as not recording to allow retry
            self.recording = False
            self.recording_start_time = None
            logger.error(f"[{self.serial}] Stop recording failed after retries")
            return False

        except Exception as e:
            logger.error(f"[{self.serial}] Stop recording failed: {e}")
            self.recording = False
            self.recording_start_time = None
            return False

    async def enable_wifi(self) -> bool:
        """Enable WiFi AP for downloads"""
        try:
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot enable WiFi: Not connected")
                return False

            logger.info(f"[{self.serial}] Enabling WiFi...")
            loop = asyncio.get_event_loop()
            _, err = await loop.run_in_executor(
                None,
                self._ble_cmd_in_thread,
                self.gopro.ble_command.enable_wifi_ap,
                (True,),
                10
            )
            if err and err != "timeout":
                raise err
            await asyncio.sleep(3)
            logger.info(f"[{self.serial}] WiFi enabled")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] WiFi enable failed: {e}")
            return False

    async def start_webcam(self) -> dict:
        """Start webcam mode for live preview"""
        try:
            self.update_connection_status()
            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot start webcam: Not connected")
                return {"success": False, "error": "Not connected"}

            logger.info(f"[{self.serial}] Starting webcam/preview mode...")

            if self.recording:
                logger.info(f"[{self.serial}] Camera is recording - preview will show live recording feed")
            else:
                logger.info(f"[{self.serial}] Enabling preview mode (camera not recording)")
                self.gopro._encoding_started.set()
                await asyncio.sleep(0.2)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    self._ble_cmd_in_thread,
                    self.gopro.ble_command.set_shutter,
                    (Params.Shutter.OFF,),
                    10
                )
                await asyncio.sleep(1)

            preview_url = "http://10.5.5.9:8554/live/amba.m3u8"

            logger.info(f"[{self.serial}] Preview ready (recording={self.recording})")
            logger.info(f"[{self.serial}] Stream URL: {preview_url}")

            return {
                "success": True,
                "stream_url": preview_url,
                "preview_url": "http://10.5.5.9:8080/gopro/camera/stream/start",
                "serial": self.serial,
                "recording": self.recording
            }

        except Exception as e:
            logger.error(f"[{self.serial}] Webcam start failed: {e}")
            return {"success": False, "error": str(e)}

    async def stop_webcam(self) -> bool:
        """Stop webcam mode"""
        try:
            if not self.connected or not self.gopro:
                return False
            logger.info(f"[{self.serial}] Webcam stopped")
            return True
        except Exception as e:
            logger.error(f"[{self.serial}] Webcam stop failed: {e}")
            return False

    async def get_battery_level(self) -> Optional[int]:
        """Get battery percentage via BLE (0-100)"""
        try:
            self.update_connection_status()
            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                return None

            loop = asyncio.get_event_loop()
            resp, err = await loop.run_in_executor(
                None,
                self._ble_cmd_in_thread,
                self.gopro.ble_status.int_batt_per.get_value,
                (),
                5
            )
            if err:
                raise Exception(str(err))
            level = resp.data.get(StatusId.INT_BATT_PER)
            if level is not None:
                self.battery_level = int(level)
                self.battery_history.append((datetime.now(), self.battery_level))
                # Keep last 60 entries (~1 hour at 60s polling)
                if len(self.battery_history) > 60:
                    self.battery_history = self.battery_history[-60:]
                return self.battery_level
        except Exception as e:
            logger.debug(f"[{self.serial}] Battery query failed: {e}")
        return self.battery_level

    def _calc_battery_drain_rate(self) -> Optional[float]:
        """Calculate battery drain rate in %/hour from history"""
        if len(self.battery_history) < 2:
            return None
        oldest_time, oldest_level = self.battery_history[0]
        newest_time, newest_level = self.battery_history[-1]
        elapsed_hours = (newest_time - oldest_time).total_seconds() / 3600
        if elapsed_hours < 0.01:  # Less than 36 seconds
            return None
        return round((oldest_level - newest_level) / elapsed_hours, 1)

    async def _get_status_value(self, status_accessor) -> Optional[any]:
        """Safely get a single BLE status value"""
        try:
            loop = asyncio.get_event_loop()
            resp, err = await loop.run_in_executor(
                None,
                self._ble_cmd_in_thread,
                status_accessor.get_value,
                (),
                5
            )
            if err:
                return None
            # Extract value from response data
            for key, val in resp.data.items():
                return val
        except Exception:
            return None

    async def get_health_status(self) -> dict:
        """Query all health metrics in one call"""
        self.update_connection_status()
        if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
            return {"error": "Not connected"}

        health = {
            "serial": self.serial,
            "name": self.name,
            "connected": self.connected,
            "recording": self.recording,
        }

        async def _safe_status(attr_name, default=None):
            """Safely read a BLE status attribute, returning default if attr doesn't exist"""
            try:
                attr = getattr(self.gopro.ble_status, attr_name, None)
                if attr is None:
                    return default
                return await self._get_status_value(attr)
            except Exception:
                return default

        # Battery (use cached + refresh)
        battery = await _safe_status("int_batt_per")
        if battery is not None:
            self.battery_level = int(battery)
            self.battery_history.append((datetime.now(), self.battery_level))
            if len(self.battery_history) > 60:
                self.battery_history = self.battery_history[-60:]
        health["battery_percent"] = self.battery_level
        health["battery_drain_rate"] = self._calc_battery_drain_rate()

        # Storage
        health["storage_remaining_kb"] = await _safe_status("space_rem")
        health["video_remaining_min"] = await _safe_status("video_rem")
        sd_raw = await _safe_status("sd_status")
        health["sd_status"] = str(sd_raw) if sd_raw is not None else None

        # Recording
        health["recording_duration_sec"] = await _safe_status("video_progress")
        encoding_raw = await _safe_status("encoding")
        health["is_encoding"] = bool(encoding_raw) if encoding_raw is not None else self.recording

        # Thermal
        hot_raw = await _safe_status("system_hot")
        health["system_hot"] = bool(hot_raw) if hot_raw is not None else False
        cold_raw = await _safe_status("video_low_temp")
        health["too_cold"] = bool(cold_raw) if cold_raw is not None else False
        thermal_raw = await _safe_status("thermal_mit_mode")
        health["thermal_mitigation"] = bool(thermal_raw) if thermal_raw is not None else False

        # GPS
        gps_raw = await _safe_status("gps_stat")
        health["gps_lock"] = bool(gps_raw) if gps_raw is not None else False

        # Media counts
        health["num_videos"] = await _safe_status("num_total_video")
        health["num_photos"] = await _safe_status("num_total_photo")

        # Orientation
        orient_raw = await _safe_status("orientation")
        health["orientation"] = str(orient_raw) if orient_raw is not None else None

        return health

    async def get_current_settings(self) -> dict:
        """Read current camera settings for preset capture"""
        self.update_connection_status()
        if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
            return {"error": "Not connected"}

        settings = {}
        setting_map = {
            "resolution": self.gopro.ble_setting.resolution,
            "fps": self.gopro.ble_setting.fps,
            "video_fov": self.gopro.ble_setting.video_field_of_view,
            "hypersmooth": self.gopro.ble_setting.hypersmooth,
            "anti_flicker": self.gopro.ble_setting.anti_flicker,
        }

        for name, setting in setting_map.items():
            try:
                loop = asyncio.get_event_loop()
                resp, err = await loop.run_in_executor(
                    None,
                    self._ble_cmd_in_thread,
                    setting.get_value,
                    (),
                    5
                )
                if not err and resp and resp.data:
                    for key, val in resp.data.items():
                        settings[name] = str(val.name) if hasattr(val, 'name') else str(val)
                        break
                else:
                    settings[name] = None
            except Exception as e:
                logger.debug(f"[{self.serial}] Failed to read setting {name}: {e}")
                settings[name] = None

        return settings

    async def apply_settings(self, settings: dict) -> dict:
        """Apply a preset's settings to this camera"""
        self.update_connection_status()
        if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
            return {"error": "Not connected"}

        results = {}
        setting_map = {
            "resolution": (self.gopro.ble_setting.resolution, Params.Resolution),
            "fps": (self.gopro.ble_setting.fps, Params.FPS),
            "video_fov": (self.gopro.ble_setting.video_field_of_view, Params.VideoFOV),
            "hypersmooth": (self.gopro.ble_setting.hypersmooth, Params.HypersmoothMode),
            "anti_flicker": (self.gopro.ble_setting.anti_flicker, Params.AntiFlicker),
        }

        for name, value_str in settings.items():
            if name not in setting_map or value_str is None:
                continue
            setting_accessor, params_enum = setting_map[name]
            try:
                # Look up the enum value by name
                param_value = params_enum[value_str]
                loop = asyncio.get_event_loop()
                _, err = await loop.run_in_executor(
                    None,
                    self._ble_cmd_in_thread,
                    setting_accessor.set,
                    (param_value,),
                    10
                )
                if err and err != "timeout":
                    results[name] = f"error: {err}"
                else:
                    results[name] = "ok"
            except (KeyError, ValueError) as e:
                results[name] = f"invalid value: {value_str}"
            except Exception as e:
                results[name] = f"error: {e}"

        return results

    def update_connection_status(self) -> bool:
        """Check and update actual connection status from gopro object.

        Checks both the open_gopro handle and the underlying Bleak client's
        actual OS-level connection state to detect stale connections (e.g.
        camera powered off).
        """
        if self.gopro and self.gopro.is_ble_connected:
            # is_ble_connected only checks _handle is not None — it can be stale.
            # Also check the underlying Bleak client's real connection state.
            try:
                bleak_client = self.gopro._ble._handle
                if bleak_client and not bleak_client.is_connected:
                    logger.info(f"[{self.serial}] Stale BLE handle detected (Bleak reports disconnected), cleaning up")
                    try:
                        self.gopro._ble._handle = None
                    except Exception:
                        pass
                    self.connected = False
                    self.recording = False
                    return False
            except (AttributeError, Exception):
                pass  # Can't check Bleak state, fall through to normal check
            if not self.connected:
                logger.info(f"[{self.serial}] Detected existing BLE connection")
            self.connected = True
            return True
        else:
            if self.connected:
                logger.info(f"[{self.serial}] Connection lost")
            self.connected = False
            return False

    def probe_ble_alive(self) -> bool:
        """Actively probe BLE connection by attempting a lightweight read.

        Returns True if the camera responds, False if it appears dead.
        This is more expensive than update_connection_status() — call
        periodically (e.g. every 15s), not on every poll.
        """
        if not self.gopro or not self.connected:
            return False
        try:
            # Try sending a keep-alive — this does an actual BLE write
            result, err = self._ble_cmd_in_thread(
                self.gopro.keep_alive, timeout=5
            )
            if err:
                logger.info(f"[{self.serial}] BLE probe failed: {err}")
                return False
            return True
        except Exception as e:
            logger.info(f"[{self.serial}] BLE probe exception: {e}")
            return False

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        self.update_connection_status()
        return {
            "serial": self.serial,
            "name": self.name,
            "wifi_ssid": self.wifi_ssid,
            "connected": self.connected,
            "recording": self.recording,
            "battery_level": self.battery_level
        }


class CameraManager:
    def __init__(self):
        self.cameras: Dict[str, CameraInstance] = {}
        self.ble_busy = False  # Set True while shutter commands are in flight to pause polling

    def add_camera(self, serial: str, wifi_ssid: str, wifi_password: str, name: str = "") -> bool:
        if serial in self.cameras:
            return False
        self.cameras[serial] = CameraInstance(serial, wifi_ssid, wifi_password, name)
        return True

    def update_camera_name(self, serial: str, name: str) -> bool:
        """Update a camera's display name and persist to saved_cameras.json"""
        if serial not in self.cameras:
            return False
        self.cameras[serial].name = name
        self._save_to_json()
        return True

    def _save_to_json(self):
        """Persist current camera list to saved_cameras.json"""
        import json
        saved_cameras_file = Path(__file__).parent.parent.parent / "saved_cameras.json"
        try:
            cameras_data = []
            for serial, cam in self.cameras.items():
                cameras_data.append({
                    "serial": cam.serial,
                    "name": cam.name,
                    "wifi_ssid": cam.wifi_ssid,
                    "wifi_password": cam.wifi_password,
                })
            with open(saved_cameras_file, 'w') as f:
                json.dump({"cameras": cameras_data}, f, indent=2)
            logger.info(f"Saved {len(cameras_data)} camera(s) to saved_cameras.json")
        except Exception as e:
            logger.error(f"Failed to save cameras to JSON: {e}")

    async def remove_camera(self, serial: str) -> bool:
        if serial in self.cameras:
            camera = self.cameras[serial]
            await camera.disconnect()
            del self.cameras[serial]
            return True
        return False

    def get_camera(self, serial: str) -> Optional[CameraInstance]:
        return self.cameras.get(serial)

    def list_cameras(self) -> List[dict]:
        return [cam.to_dict() for cam in self.cameras.values()]

    async def check_existing_connections(self) -> Dict[str, bool]:
        """Check for existing BLE connections (fast 1s timeout per camera)"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
                results[serial] = True
                continue
            tasks.append(self._check_single_connection(serial, camera))
            serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        connected_count = sum(1 for v in results.values() if v)
        if connected_count > 0:
            logger.info(f"Found {connected_count} existing BLE connections")

        return results

    async def _check_single_connection(self, serial: str, camera: CameraInstance) -> bool:
        """Check a single camera for existing connection.

        Uses minimal retries (1 scan, 8s timeout) so it finishes quickly
        and doesn't block the BLE singleton for minutes on cameras that are off.
        """
        try:
            last4 = serial[-4:] if len(serial) > 4 else serial
            ble_target = f"GoPro {last4}"
            logger.info(f"[{serial}] Quick check for existing BLE connection (target: {ble_target})...")
            camera.gopro = GoPro(target=ble_target, enable_wifi=False)

            loop = asyncio.get_event_loop()
            # Use retries=1, timeout=5 so scan finishes fast for missing cameras
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: camera.gopro.open(timeout=5, retries=1)
                ),
                timeout=8
            )

            # Disable _maintain_ble to prevent _ready lock blocking BLE commands
            camera.gopro._maintain_ble = False
            camera.connected = True
            logger.info(f"[{serial}] Found existing BLE connection!")
            return True

        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"[{serial}] No existing connection: {e}")
            if camera.gopro:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, camera.gopro.close)
                except Exception:
                    pass
                camera.gopro = None
            camera.connected = False
            return False

    async def connect_all(self) -> Dict[str, bool]:
        """Connect to all cameras via BLE — sequentially to avoid macOS CoreBluetooth conflicts"""
        results = {}

        already_connected = []
        needs_connection = []

        for serial, camera in self.cameras.items():
            if camera.connected and camera.gopro and camera.gopro.is_ble_connected:
                already_connected.append(serial)
                results[serial] = True
            else:
                needs_connection.append(serial)

        if already_connected:
            logger.info(f"Already connected: {', '.join(already_connected)}")

        if needs_connection:
            logger.info(f"Connecting sequentially to: {', '.join(needs_connection)}")
            for serial in needs_connection:
                camera = self.cameras[serial]
                try:
                    success = await camera.connect_ble()
                    results[serial] = success
                    if success:
                        logger.info(f"[{serial}] Connected, moving to next camera...")
                    else:
                        logger.warning(f"[{serial}] Failed, moving to next camera...")
                except Exception as e:
                    logger.error(f"[{serial}] Connection exception: {e}")
                    results[serial] = False
        else:
            logger.info("All cameras already connected!")

        return results

    async def disconnect_all(self):
        tasks = [cam.disconnect() for cam in self.cameras.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_recording_all(self) -> Dict[str, bool]:
        """Start recording on all connected cameras — rapid sequential to avoid BLE contention.

        Uses raw BLE writes (fire-and-forget) for near-simultaneous start.
        All cameras receive the shutter command within milliseconds of each other.
        """
        connected = {s: c for s, c in self.cameras.items() if c.connected}
        if not connected:
            return {}

        serials = list(connected.keys())
        logger.info(f"Firing START shutter to {len(serials)} cameras (raw BLE write)...")
        t0 = time.monotonic()

        results = {}
        for serial in serials:
            camera = connected[serial]
            try:
                ok = camera._fire_shutter_raw(Params.Shutter.ON)
                results[serial] = ok
            except Exception as e:
                logger.error(f"[{serial}] Start recording exception: {e}")
                results[serial] = False

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"All shutter commands fired in {elapsed_ms:.0f}ms")

        return results

    async def stop_recording_all(self) -> Dict[str, bool]:
        """Stop recording on all connected cameras — raw BLE write for simultaneous stop."""
        connected = {s: c for s, c in self.cameras.items() if c.connected}
        if not connected:
            return {}

        serials = list(connected.keys())
        logger.info(f"Firing STOP shutter to {len(serials)} cameras (raw BLE write)...")
        t0 = time.monotonic()

        results = {}
        for serial in serials:
            camera = connected[serial]
            try:
                ok = camera._fire_shutter_raw(Params.Shutter.OFF)
                results[serial] = ok
            except Exception as e:
                logger.error(f"[{serial}] Stop recording exception: {e}")
                results[serial] = False

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"All stop commands fired in {elapsed_ms:.0f}ms")

        return results

    async def enable_wifi_all(self) -> Dict[str, bool]:
        """Enable WiFi on all connected cameras — sequential for BLE stability"""
        results = {}
        for serial, camera in self.cameras.items():
            if camera.connected:
                try:
                    results[serial] = await camera.enable_wifi()
                except Exception as e:
                    logger.error(f"[{serial}] Enable WiFi exception: {e}")
                    results[serial] = False
        return results

    async def start_preview_all(self) -> Dict[str, dict]:
        results = {}
        for serial, camera in self.cameras.items():
            if camera.connected:
                try:
                    result = await camera.start_webcam()
                    results[serial] = result if isinstance(result, dict) else {"success": False, "error": "Unknown error"}
                except Exception as e:
                    results[serial] = {"success": False, "error": str(e)}
        return results

    async def stop_preview_all(self) -> Dict[str, bool]:
        results = {}
        for serial, camera in self.cameras.items():
            if camera.connected:
                try:
                    results[serial] = await camera.stop_webcam()
                except Exception as e:
                    results[serial] = False
        return results

    async def get_all_health(self) -> dict:
        """Get health for all connected cameras — sequential for BLE stability"""
        results = {}
        for serial, camera in self.cameras.items():
            if camera.connected:
                try:
                    results[serial] = await camera.get_health_status()
                except Exception as e:
                    logger.error(f"[{serial}] Health query failed: {e}")
                    results[serial] = {"error": str(e), "serial": serial, "name": camera.name}
            else:
                results[serial] = {
                    "serial": serial,
                    "name": camera.name,
                    "connected": False,
                    "recording": False,
                    "battery_percent": camera.battery_level,
                }
        return results

    async def get_all_battery_levels(self) -> Dict[str, Optional[int]]:
        """Query battery levels from all connected cameras — sequential for BLE stability"""
        results = {}
        for serial, camera in self.cameras.items():
            if camera.connected:
                try:
                    result = await camera.get_battery_level()
                    results[serial] = result if isinstance(result, int) else camera.battery_level
                except Exception:
                    results[serial] = camera.battery_level
            else:
                results[serial] = camera.battery_level  # Return cached value
        return results

    async def discover_cameras(self, timeout: int = 30) -> List[dict]:
        """Auto-discover GoPro cameras via BLE scan"""
        discovered = []
        try:
            logger.info(f"Scanning for GoPro cameras (timeout: {timeout}s)...")
            import re
            from bleak import BleakScanner
            devices = await BleakScanner.discover(timeout=timeout)

            logger.info(f"Found {len(devices)} total BLE devices")

            for device in devices:
                if device.name and "GoPro" in device.name:
                    logger.info(f"Found potential GoPro: {device.name} ({device.address})")
                    serial_match = re.search(r'GoPro\s*(\d{4})', device.name)
                    if serial_match:
                        serial = serial_match.group(1)
                        discovered.append({
                            "serial": serial,
                            "name": device.name,
                            "address": device.address,
                            "rssi": getattr(device, 'rssi', None)
                        })
                        logger.info(f"Added GoPro: serial={serial}, name={device.name}")
                    else:
                        digits = re.findall(r'\d+', device.name)
                        if digits:
                            serial = digits[0][-4:] if len(digits[0]) >= 4 else digits[0]
                            discovered.append({
                                "serial": serial,
                                "name": device.name,
                                "address": device.address,
                                "rssi": getattr(device, 'rssi', None)
                            })

            logger.info(f"Discovery complete: Found {len(discovered)} GoPro camera(s)")

        except PermissionError as e:
            logger.error(f"Bluetooth permission denied! Grant access in System Settings > Privacy & Security > Bluetooth")
            logger.error(f"Error: {e}")
        except Exception as e:
            logger.error(f"Camera discovery failed: {e}", exc_info=True)

        return discovered
