"""
GoPro Camera Management via BLE
"""
import asyncio
from typing import Dict, List, Optional
from open_gopro import WirelessGoPro
from open_gopro.models import constants
import logging

logger = logging.getLogger(__name__)


class CameraInstance:
    def __init__(self, serial: str, wifi_ssid: str, wifi_password: str, name: str = ""):
        self.serial = serial
        self.wifi_ssid = wifi_ssid
        self.wifi_password = wifi_password
        self.name = name or f"GoPro {serial}"
        self.gopro: Optional[WirelessGoPro] = None
        self.connected = False
        self.recording = False

    async def connect_ble(self) -> bool:
        """Connect via BLE only - uses same logic as working script"""
        try:
            # Check if already connected
            if self.connected and self.gopro and self.gopro.is_open:
                logger.info(f"[{self.serial}] Already connected, skipping reconnection")
                return True

            logger.info(f"[{self.serial}] Connecting BLE...")

            # Use exact same parameters as working script
            self.gopro = WirelessGoPro(target=self.serial, enable_wifi=False)

            logger.info(f"[{self.serial}] Opening connection (timeout: 60s)...")
            await asyncio.wait_for(self.gopro.open(), timeout=60)

            logger.info(f"[{self.serial}] Connection opened, disabling WiFi...")

            # Turn off WiFi initially (ignore errors like in working script)
            try:
                await self.gopro.ble_command.enable_wifi_ap(enable=False)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"[{self.serial}] WiFi disable warning (ignoring): {e}")

            self.connected = True
            logger.info(f"[{self.serial}] ✅ BLE connected successfully")
            return True

        except asyncio.TimeoutError:
            logger.error(f"[{self.serial}] ❌ BLE connection timeout after 60s")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"[{self.serial}] ❌ BLE connection failed: {type(e).__name__}: {e}")
            self.connected = False
            return False

    async def disconnect(self):
        """Disconnect BLE"""
        if self.gopro and self.gopro.is_open:
            try:
                await self.gopro.close()
                self.connected = False
                logger.info(f"[{self.serial}] Disconnected")
            except Exception as e:
                logger.error(f"[{self.serial}] Disconnect error: {e}")

    async def start_recording(self) -> bool:
        """Start recording"""
        try:
            # Verify connection is actually open
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_open:
                logger.error(f"[{self.serial}] Cannot record: Not connected (connected={self.connected}, gopro_open={self.gopro.is_open if self.gopro else False})")
                return False

            logger.info(f"[{self.serial}] Starting recording...")
            await self.gopro.ble_command.set_shutter(shutter=constants.Toggle.ENABLE)
            self.recording = True
            logger.info(f"[{self.serial}] Recording started")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] Start recording failed: {e}")
            self.connected = False  # Mark as disconnected if command fails
            return False

    async def stop_recording(self) -> bool:
        """Stop recording"""
        try:
            # Verify connection is actually open
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_open:
                logger.error(f"[{self.serial}] Cannot stop recording: Not connected")
                return False

            logger.info(f"[{self.serial}] Stopping recording...")
            await self.gopro.ble_command.set_shutter(shutter=constants.Toggle.DISABLE)
            await asyncio.sleep(5)  # Wait for file to save
            self.recording = False
            logger.info(f"[{self.serial}] Recording stopped")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] Stop recording failed: {e}")
            self.connected = False  # Mark as disconnected if command fails
            return False

    async def enable_wifi(self) -> bool:
        """Enable WiFi AP for downloads"""
        try:
            # Verify connection is actually open
            self.update_connection_status()

            if not self.connected or not self.gopro or not self.gopro.is_open:
                logger.error(f"[{self.serial}] Cannot enable WiFi: Not connected")
                return False

            logger.info(f"[{self.serial}] Enabling WiFi...")
            await self.gopro.ble_command.enable_wifi_ap(enable=True)
            await asyncio.sleep(3)
            logger.info(f"[{self.serial}] WiFi enabled")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] WiFi enable failed: {e}")
            self.connected = False  # Mark as disconnected if command fails
            return False

    async def start_webcam(self) -> dict:
        """Start webcam mode for live preview - does NOT stop recording if already recording"""
        try:
            # Verify connection
            self.update_connection_status()
            if not self.connected or not self.gopro or not self.gopro.is_open:
                logger.error(f"[{self.serial}] Cannot start webcam: Not connected")
                return {"success": False, "error": "Not connected"}

            logger.info(f"[{self.serial}] Starting webcam/preview mode...")

            # IMPORTANT: If camera is recording, DON'T stop the shutter!
            # The preview stream is available during recording.
            if self.recording:
                logger.info(f"[{self.serial}] Camera is recording - preview will show live recording feed")
            else:
                # If not recording, enable preview mode by disabling shutter
                logger.info(f"[{self.serial}] Enabling preview mode (camera not recording)")
                await self.gopro.ble_command.set_shutter(shutter=constants.Toggle.DISABLE)
                await asyncio.sleep(1)

            # GoPro preview stream is always available at this URL when WiFi is connected
            # Works both during recording and in preview mode
            preview_url = f"http://10.5.5.9:8554/live/amba.m3u8"

            logger.info(f"[{self.serial}] ✅ Preview ready (recording={self.recording})")
            logger.info(f"[{self.serial}] Stream URL: {preview_url}")

            return {
                "success": True,
                "stream_url": preview_url,
                "preview_url": f"http://10.5.5.9:8080/gopro/camera/stream/start",
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

            logger.info(f"[{self.serial}] Stopping webcam mode...")
            # Webcam mode stops automatically when disconnected
            logger.info(f"[{self.serial}] Webcam stopped")
            return True

        except Exception as e:
            logger.error(f"[{self.serial}] Webcam stop failed: {e}")
            return False

    def update_connection_status(self) -> bool:
        """Check and update actual connection status from gopro object"""
        if self.gopro and self.gopro.is_open:
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
        """Convert to dictionary - updates connection status first"""
        # Update connection status from actual BLE state
        self.update_connection_status()

        return {
            "serial": self.serial,
            "name": self.name,
            "wifi_ssid": self.wifi_ssid,
            "wifi_password": self.wifi_password,
            "connected": self.connected,
            "recording": self.recording
        }


class CameraManager:
    def __init__(self):
        self.cameras: Dict[str, CameraInstance] = {}

    def add_camera(self, serial: str, wifi_ssid: str, wifi_password: str, name: str = "") -> bool:
        """Add a camera to the list"""
        if serial in self.cameras:
            return False

        self.cameras[serial] = CameraInstance(serial, wifi_ssid, wifi_password, name)
        return True

    def remove_camera(self, serial: str) -> bool:
        """Remove a camera from the list"""
        if serial in self.cameras:
            del self.cameras[serial]
            return True
        return False

    def get_camera(self, serial: str) -> Optional[CameraInstance]:
        """Get camera by serial"""
        return self.cameras.get(serial)

    def list_cameras(self) -> List[dict]:
        """List all cameras - checks actual connection status"""
        return [cam.to_dict() for cam in self.cameras.values()]

    async def check_existing_connections(self) -> Dict[str, bool]:
        """
        Try to reconnect to cameras that might already be paired/connected in the system.
        This is useful when the app restarts but cameras are still connected via BLE.
        Runs checks in parallel for speed.
        """
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            # Skip if already connected in our app
            if camera.connected and camera.gopro and camera.gopro.is_open:
                results[serial] = True
                continue

            # Add to parallel check
            tasks.append(self._check_single_connection(serial, camera))
            serials.append(serial)

        # Run all checks in parallel for speed
        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        connected_count = sum(1 for v in results.values() if v)
        if connected_count > 0:
            logger.info(f"Found {connected_count} existing BLE connections")

        return results

    async def _check_single_connection(self, serial: str, camera: CameraInstance) -> bool:
        """Check a single camera for existing connection - FAST 1 second timeout"""
        try:
            logger.info(f"[{serial}] ⚡ Quick check for existing BLE connection...")
            camera.gopro = WirelessGoPro(target=serial, enable_wifi=False)

            # Use 1 second timeout for INSTANT detection
            await asyncio.wait_for(camera.gopro.open(), timeout=1)

            camera.connected = True
            logger.info(f"[{serial}] ✅ INSTANT: Found existing BLE connection!")
            return True

        except asyncio.TimeoutError:
            logger.debug(f"[{serial}] No existing connection (1s timeout)")
            camera.connected = False
            return False
        except Exception as e:
            logger.debug(f"[{serial}] No existing connection: {e}")
            camera.connected = False
            return False

    async def connect_all(self) -> Dict[str, bool]:
        """Connect to all cameras via BLE"""
        results = {}
        tasks = []
        serials = []

        # Separate already-connected from needs-connection
        already_connected = []
        needs_connection = []

        for serial, camera in self.cameras.items():
            if camera.connected and camera.gopro and camera.gopro.is_open:
                already_connected.append(serial)
                results[serial] = True  # Already connected
            else:
                needs_connection.append(serial)
                tasks.append(self._connect_camera(serial, camera))
                serials.append(serial)

        if already_connected:
            logger.info(f"Already connected: {', '.join(already_connected)}")

        if needs_connection:
            logger.info(f"Connecting to: {', '.join(needs_connection)}")
            task_results = await asyncio.gather(*tasks, return_exceptions=True)

            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False
        else:
            logger.info("All cameras already connected!")

        return results

    async def _connect_camera(self, serial: str, camera: CameraInstance) -> bool:
        """Helper to connect a single camera"""
        return await camera.connect_ble()

    async def disconnect_all(self):
        """Disconnect all cameras"""
        tasks = [cam.disconnect() for cam in self.cameras.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_recording_all(self) -> Dict[str, bool]:
        """Start recording on all connected cameras"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected:
                tasks.append(camera.start_recording())
                serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        return results

    async def stop_recording_all(self) -> Dict[str, bool]:
        """Stop recording on all cameras"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected:
                tasks.append(camera.stop_recording())
                serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        return results

    async def enable_wifi_all(self) -> Dict[str, bool]:
        """Enable WiFi on all cameras"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected:
                tasks.append(camera.enable_wifi())
                serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        return results

    async def start_preview_all(self) -> Dict[str, dict]:
        """Start preview/webcam mode on all connected cameras"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected:
                tasks.append(camera.start_webcam())
                serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                if isinstance(result, dict):
                    results[serial] = result
                else:
                    results[serial] = {"success": False, "error": "Unknown error"}

        return results

    async def stop_preview_all(self) -> Dict[str, bool]:
        """Stop preview/webcam mode on all cameras"""
        results = {}
        tasks = []
        serials = []

        for serial, camera in self.cameras.items():
            if camera.connected:
                tasks.append(camera.stop_webcam())
                serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        return results

    async def discover_cameras(self, timeout: int = 30) -> List[dict]:
        """
        Auto-discover GoPro cameras via BLE scan
        Uses same logic as the working script
        """
        discovered = []

        try:
            logger.info(f"Scanning for GoPro cameras (timeout: {timeout}s)...")
            logger.info("Make sure GoPro cameras are powered on and in pairing mode")

            # Use open-gopro's built-in scanner for better compatibility
            from open_gopro import WirelessGoPro
            import re

            # Scan for BLE devices
            from bleak import BleakScanner
            devices = await BleakScanner.discover(timeout=timeout)

            logger.info(f"Found {len(devices)} total BLE devices")

            for device in devices:
                # Log all devices for debugging
                if device.name:
                    logger.debug(f"BLE Device: {device.name} ({device.address})")

                # GoPro cameras have specific patterns:
                # - "GoPro XXXX" (where XXXX is last 4 of serial)
                # - Device must be connectable
                if device.name and "GoPro" in device.name:
                    logger.info(f"Found potential GoPro: {device.name} ({device.address})")

                    # Extract serial from device name
                    # Pattern: "GoPro 8881" or "GoPro8881"
                    serial_match = re.search(r'GoPro\s*(\d{4})', device.name)

                    if serial_match:
                        serial = serial_match.group(1)
                        discovered.append({
                            "serial": serial,
                            "name": device.name,
                            "address": device.address,
                            "rssi": getattr(device, 'rssi', None)
                        })
                        logger.info(f"✓ Added GoPro: serial={serial}, name={device.name}")
                    else:
                        # Fallback: try to extract any 4 digits
                        digits = re.findall(r'\d+', device.name)
                        if digits:
                            serial = digits[0][-4:] if len(digits[0]) >= 4 else digits[0]
                            discovered.append({
                                "serial": serial,
                                "name": device.name,
                                "address": device.address,
                                "rssi": getattr(device, 'rssi', None)
                            })
                            logger.info(f"✓ Added GoPro (fallback): serial={serial}, name={device.name}")
                        else:
                            logger.warning(f"Could not extract serial from: {device.name}")

            logger.info(f"=" * 60)
            logger.info(f"Discovery complete: Found {len(discovered)} GoPro camera(s)")
            for cam in discovered:
                logger.info(f"  - {cam['name']} (Serial: {cam['serial']}, Address: {cam['address']})")
            logger.info(f"=" * 60)

        except PermissionError as e:
            logger.error(f"❌ Bluetooth permission denied!")
            logger.error(f"Grant Bluetooth access: System Settings > Privacy & Security > Bluetooth")
            logger.error(f"Error: {e}")
        except Exception as e:
            logger.error(f"❌ Camera discovery failed: {e}", exc_info=True)

        return discovered
