"""
Cross-platform WiFi Management
"""
import subprocess
import platform
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WiFiManager:
    def __init__(self):
        self.system = platform.system()
        self._original_wifi_ip = None  # Track original network by IP/gateway

    def get_current_wifi(self) -> Optional[str]:
        """Get current WiFi SSID (may return None on macOS 26+ due to privacy)"""
        try:
            if self.system == "Darwin":  # macOS
                result = subprocess.run(
                    ["networksetup", "-getairportnetwork", "en0"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if "Current Wi-Fi Network:" in result.stdout:
                    return result.stdout.split("Current Wi-Fi Network:")[1].strip()

            elif self.system == "Windows":
                result = subprocess.run(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in result.stdout.split('\n'):
                    if 'SSID' in line and 'BSSID' not in line:
                        return line.split(':')[1].strip()

            elif self.system == "Linux":
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in result.stdout.split('\n'):
                    if line.startswith('yes:'):
                        return line.split(':')[1]

        except Exception as e:
            logger.warning(f"Error getting current WiFi: {e}")

        return None

    def get_current_ip(self) -> Optional[str]:
        """Get current IP address (cross-platform)"""
        try:
            if self.system == "Darwin":  # macOS
                result = subprocess.run(
                    ["ipconfig", "getifaddr", "en0"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            elif self.system == "Windows":
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(("10.255.255.255", 1))
                    ip = s.getsockname()[0]
                finally:
                    s.close()
                return ip
            elif self.system == "Linux":
                result = subprocess.run(
                    ["hostname", "-I"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
        except Exception:
            pass
        return None

    def is_on_gopro_network(self) -> bool:
        """Check if currently connected to GoPro WiFi (10.5.5.x subnet)"""
        ip = self.get_current_ip()
        return ip is not None and ip.startswith("10.5.5.")

    def connect_wifi(self, ssid: str, password: str, timeout: int = 30) -> bool:
        """Connect to WiFi network"""
        logger.info("=" * 60)
        logger.info(f"WiFi Connection Request")
        logger.info(f"   Target SSID: {ssid}")
        logger.info(f"   Platform: {self.system}")
        logger.info("=" * 60)

        try:
            if self.system == "Darwin":  # macOS
                return self._connect_macos(ssid, password, timeout)
            elif self.system == "Windows":
                return self._connect_windows(ssid, password, timeout)
            elif self.system == "Linux":
                return self._connect_linux(ssid, password, timeout)
            else:
                logger.error(f"Unsupported platform: {self.system}")
                return False

        except Exception as e:
            logger.error(f"WiFi connection error: {e}", exc_info=True)
            return False

    def _connect_macos(self, ssid: str, password: str, timeout: int) -> bool:
        """macOS WiFi connection — fixed for macOS 26+

        Key insight: On macOS 26, networksetup -setairportnetwork reports success
        but doesn't actually switch if already connected to a preferred network.
        Solution: Use CoreWLAN disassociate() to drop the current connection first,
        then immediately run networksetup to connect before auto-reconnect kicks in.
        """
        import requests

        logger.info(f"\nmacOS WiFi Connection Process")
        logger.info("-" * 60)

        self._original_wifi_ip = self.get_current_ip()
        logger.info(f"Original IP: {self._original_wifi_ip}")

        # Step 1: Disassociate from current network via CoreWLAN
        # This drops the connection but keeps WiFi radio on (no auto-reconnect race)
        logger.info("Step 1: Disassociating from current network...")
        try:
            import objc
            CoreWLAN = objc.loadBundle(
                'CoreWLAN', {},
                bundle_path='/System/Library/Frameworks/CoreWLAN.framework'
            )
            CWWiFiClient = objc.lookUpClass('CWWiFiClient')
            client = CWWiFiClient.sharedWiFiClient()
            iface = client.interface()
            iface.disassociate()
            logger.info("   Disassociated")
        except Exception as e:
            logger.warning(f"   CoreWLAN disassociate failed: {e}")
            # Fallback: turn WiFi off/on
            logger.info("   Fallback: cycling WiFi off/on...")
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "off"],
                capture_output=True, timeout=10
            )
            time.sleep(2)
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "on"],
                capture_output=True, timeout=10
            )
            time.sleep(1)

        # Step 2: Connect IMMEDIATELY (before macOS auto-reconnects to preferred network)
        logger.info(f"Step 2: Connecting to {ssid} (30s timeout)...")
        try:
            result = subprocess.run(
                ["networksetup", "-setairportnetwork", "en0", ssid, password],
                capture_output=True,
                timeout=30,
                text=True
            )
            if result.returncode == 0:
                logger.info(f"   Connection command succeeded")
            else:
                all_output = (result.stderr or "") + (result.stdout or "")
                logger.warning(f"   Command rc={result.returncode}: {all_output.strip()}")
                if "Could not find network" in all_output:
                    logger.error(f"   Network '{ssid}' not found! Is camera WiFi AP enabled?")
                    return False
        except subprocess.TimeoutExpired:
            logger.warning(f"   Timed out after 30 seconds")
        except Exception as e:
            logger.error(f"   Error: {e}")

        # Step 3: Wait for GoPro IP (10.5.5.x)
        logger.info("Step 3: Waiting for GoPro IP (10.5.5.x)...")
        for attempt in range(15):  # Up to ~30 seconds
            time.sleep(2)
            current_ip = self.get_current_ip()
            if current_ip and current_ip.startswith("10.5.5."):
                logger.info(f"   Got GoPro IP: {current_ip}")
                break
            logger.info(f"   Attempt {attempt+1}/15: IP={current_ip}")
        else:
            current_ip = self.get_current_ip()
            if not (current_ip and current_ip.startswith("10.5.5.")):
                logger.error(f"   FAILED: Not on GoPro subnet (IP: {current_ip})")
                logger.info("=" * 60)
                return False

        # Step 4: Test camera reachability
        GOPRO_IP = "http://10.5.5.9:8080"
        logger.info(f"Step 4: Testing camera reachability...")

        for attempt in range(3):
            try:
                test_resp = requests.get(f"{GOPRO_IP}/gopro/media/list", timeout=5)
                if test_resp.status_code == 200:
                    logger.info(f"   SUCCESS! Camera is reachable")
                    logger.info("=" * 60)
                    return True
                else:
                    logger.warning(f"   Unexpected status: {test_resp.status_code}")
            except requests.exceptions.Timeout:
                logger.warning(f"   Attempt {attempt+1}/3: Timeout...")
            except requests.exceptions.ConnectionError:
                logger.warning(f"   Attempt {attempt+1}/3: Connection error...")
            except Exception as e:
                logger.warning(f"   Attempt {attempt+1}/3: {e}")
            if attempt < 2:
                time.sleep(3)

        # On GoPro subnet but HTTP not responding — proceed anyway
        current_ip = self.get_current_ip()
        if current_ip and current_ip.startswith("10.5.5."):
            logger.info(f"   On GoPro subnet — proceeding anyway")
            logger.info("=" * 60)
            return True

        logger.error("   FAILED: WiFi connection unsuccessful")
        logger.info("=" * 60)
        return False

    def _connect_windows(self, ssid: str, password: str, timeout: int) -> bool:
        """Windows WiFi connection"""
        # Create profile XML
        profile_xml = f'''<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>'''

        # Save profile
        profile_path = f"wifi_profile_{ssid}.xml"
        with open(profile_path, 'w') as f:
            f.write(profile_xml)

        try:
            # Add profile
            subprocess.run(
                ["netsh", "wlan", "add", "profile", f"filename={profile_path}"],
                capture_output=True,
                timeout=10
            )

            # Connect
            subprocess.run(
                ["netsh", "wlan", "connect", f"name={ssid}"],
                capture_output=True,
                timeout=timeout
            )

            time.sleep(10)

            # Verify
            current = self.get_current_wifi()
            return current == ssid

        finally:
            # Clean up profile file
            import os
            if os.path.exists(profile_path):
                os.remove(profile_path)

    def _connect_linux(self, ssid: str, password: str, timeout: int) -> bool:
        """Linux WiFi connection using nmcli"""
        # Disconnect current
        subprocess.run(
            ["nmcli", "device", "disconnect", "wlan0"],
            capture_output=True,
            timeout=10
        )
        time.sleep(2)

        # Connect
        result = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password],
            capture_output=True,
            timeout=timeout
        )

        time.sleep(5)

        # Verify
        current = self.get_current_wifi()
        return current == ssid

    def disconnect(self) -> bool:
        """Disconnect from current WiFi"""
        try:
            if self.system == "Darwin":
                subprocess.run(
                    ["networksetup", "-setairportpower", "en0", "off"],
                    capture_output=True,
                    timeout=10
                )
                time.sleep(2)
                subprocess.run(
                    ["networksetup", "-setairportpower", "en0", "on"],
                    capture_output=True,
                    timeout=10
                )
            elif self.system == "Windows":
                subprocess.run(
                    ["netsh", "wlan", "disconnect"],
                    capture_output=True,
                    timeout=10
                )
            elif self.system == "Linux":
                subprocess.run(
                    ["nmcli", "device", "disconnect", "wlan0"],
                    capture_output=True,
                    timeout=10
                )
            return True
        except:
            return False
