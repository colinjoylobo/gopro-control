"""
GoPro Camera Management via BLE
"""
import asyncio
import threading
import time
from typing import Dict, List, Optional
from open_gopro import GoPro, Params
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
                    timeout=60
                )

                logger.info(f"[{self.serial}] Connection opened, disabling WiFi...")

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
        if self.gopro and self.gopro.is_ble_connected:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.gopro.close)
                self.connected = False
                logger.info(f"[{self.serial}] Disconnected")
            except Exception as e:
                logger.error(f"[{self.serial}] Disconnect error: {e}")

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

    async def _fire_shutter(self, shutter: "Params.Shutter") -> bool:
        """Fire shutter command once (no retry). Used for synchronized multi-camera start/stop."""
        try:
            self.update_connection_status()
            if not self.connected or not self.gopro or not self.gopro.is_ble_connected:
                logger.error(f"[{self.serial}] Cannot fire shutter: Not connected")
                return False

            is_start = (shutter == Params.Shutter.ON)
            action = "start" if is_start else "stop"
            logger.info(f"[{self.serial}] Firing shutter {action}...")

            if is_start:
                self.gopro._encoding_started.clear()
                timer = threading.Timer(3.0, self.gopro._encoding_started.set)
                timer.daemon = True
                timer.start()
            else:
                self.gopro._encoding_started.set()
                await asyncio.sleep(0.2)

            loop = asyncio.get_event_loop()
            _, err = await loop.run_in_executor(
                None,
                self._ble_cmd_in_thread,
                self.gopro.ble_command.set_shutter,
                (shutter,),
                10
            )

            if is_start:
                timer.cancel()

            if err and err != "timeout":
                raise err

            self.recording = is_start
            logger.info(f"[{self.serial}] Shutter {action} sent successfully")
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
                        return True
                    elif err:
                        raise err

                    self.recording = True
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
                    logger.info(f"[{self.serial}] Recording stopped")
                    return True
                except Exception as e:
                    logger.warning(f"[{self.serial}] Stop attempt {attempt+1} failed: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2)

            # Even if command failed, mark as not recording to allow retry
            self.recording = False
            logger.error(f"[{self.serial}] Stop recording failed after retries")
            return False

        except Exception as e:
            logger.error(f"[{self.serial}] Stop recording failed: {e}")
            self.recording = False
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
            from open_gopro.constants import StatusId
            level = resp.data.get(StatusId.INT_BATT_PER)
            if level is not None:
                self.battery_level = int(level)
                return self.battery_level
        except Exception as e:
            logger.debug(f"[{self.serial}] Battery query failed: {e}")
        return self.battery_level

    def update_connection_status(self) -> bool:
        """Check and update actual connection status from gopro object"""
        if self.gopro and self.gopro.is_ble_connected:
            if not self.connected:
                logger.info(f"[{self.serial}] Detected existing BLE connection")
            self.connected = True
            return True
        else:
            if self.connected:
                logger.info(f"[{self.serial}] Connection lost")
            self.connected = False
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

    def add_camera(self, serial: str, wifi_ssid: str, wifi_password: str, name: str = "") -> bool:
        if serial in self.cameras:
            return False
        self.cameras[serial] = CameraInstance(serial, wifi_ssid, wifi_password, name)
        return True

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
        """Check a single camera for existing connection"""
        try:
            last4 = serial[-4:] if len(serial) > 4 else serial
            ble_target = f"GoPro {last4}"
            logger.info(f"[{serial}] Quick check for existing BLE connection (target: {ble_target})...")
            camera.gopro = GoPro(target=ble_target, enable_wifi=False)

            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, camera.gopro.open),
                timeout=1
            )

            camera.connected = True
            logger.info(f"[{serial}] Found existing BLE connection!")
            return True

        except asyncio.TimeoutError:
            logger.debug(f"[{serial}] No existing connection (1s timeout)")
            if camera.gopro:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, camera.gopro.close)
                except Exception:
                    pass
                camera.gopro = None
            camera.connected = False
            return False
        except Exception as e:
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
        """Start recording on all connected cameras — concurrent for synchronized start.

        Fires BLE shutter commands to all cameras at the same time so they
        begin recording within ~200ms of each other instead of sequentially.
        """
        connected = {s: c for s, c in self.cameras.items() if c.connected}
        if not connected:
            return {}

        serials = list(connected.keys())
        logger.info(f"Firing START shutter concurrently to {len(serials)} cameras...")
        t0 = time.monotonic()

        tasks = [camera._fire_shutter(Params.Shutter.ON) for camera in connected.values()]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"All shutter commands completed in {elapsed_ms:.0f}ms")

        results = {}
        for serial, result in zip(serials, task_results):
            if isinstance(result, bool):
                results[serial] = result
            else:
                logger.error(f"[{serial}] Start recording exception: {result}")
                results[serial] = False

        # Retry any failures sequentially (fallback)
        failed = [s for s, ok in results.items() if not ok]
        if failed:
            logger.info(f"Retrying {len(failed)} failed camera(s) sequentially...")
            for serial in failed:
                try:
                    results[serial] = await connected[serial].start_recording()
                except Exception as e:
                    logger.error(f"[{serial}] Retry start recording failed: {e}")
                    results[serial] = False

        return results

    async def stop_recording_all(self) -> Dict[str, bool]:
        """Stop recording on all connected cameras — concurrent for synchronized stop."""
        connected = {s: c for s, c in self.cameras.items() if c.connected}
        if not connected:
            return {}

        serials = list(connected.keys())
        logger.info(f"Firing STOP shutter concurrently to {len(serials)} cameras...")
        t0 = time.monotonic()

        tasks = [camera._fire_shutter(Params.Shutter.OFF) for camera in connected.values()]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"All stop commands completed in {elapsed_ms:.0f}ms")

        results = {}
        for serial, result in zip(serials, task_results):
            if isinstance(result, bool):
                results[serial] = result
            else:
                logger.error(f"[{serial}] Stop recording exception: {result}")
                results[serial] = False

        # Retry any failures sequentially
        failed = [s for s, ok in results.items() if not ok]
        if failed:
            logger.info(f"Retrying {len(failed)} failed camera(s) sequentially...")
            for serial in failed:
                try:
                    results[serial] = await connected[serial].stop_recording()
                except Exception as e:
                    logger.error(f"[{serial}] Retry stop recording failed: {e}")
                    results[serial] = False

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
