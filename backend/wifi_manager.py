"""
Cross-platform WiFi Management
"""
import subprocess
import platform
import time
from typing import Optional


class WiFiManager:
    def __init__(self):
        self.system = platform.system()

    def get_current_wifi(self) -> Optional[str]:
        """Get current WiFi SSID"""
        try:
            if self.system == "Darwin":  # macOS
                result = subprocess.run(
                    ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in result.stdout.split('\n'):
                    if ' SSID:' in line:
                        return line.split('SSID:')[1].strip()

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
            print(f"Error getting current WiFi: {e}")

        return None

    def connect_wifi(self, ssid: str, password: str, timeout: int = 30) -> bool:
        """Connect to WiFi network"""
        print("=" * 60)
        print(f"📡 WiFi Connection Request")
        print(f"   Target SSID: {ssid}")
        print(f"   Platform: {self.system}")
        print("=" * 60)

        try:
            if self.system == "Darwin":  # macOS
                return self._connect_macos(ssid, password, timeout)
            elif self.system == "Windows":
                return self._connect_windows(ssid, password, timeout)
            elif self.system == "Linux":
                return self._connect_linux(ssid, password, timeout)
            else:
                print(f"❌ Unsupported platform: {self.system}")
                return False

        except Exception as e:
            print(f"❌ WiFi connection error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _connect_macos(self, ssid: str, password: str, timeout: int) -> bool:
        """
        macOS WiFi connection
        Uses EXACT same logic as final_working_script-gopro.py
        """
        import requests

        print(f"\n🍎 macOS WiFi Connection Process")
        print("-" * 60)

        # Force disconnect - turn WiFi OFF
        print("Step 1: ⏸️  Disconnecting from current WiFi...")
        try:
            result = subprocess.run(
                ["networksetup", "-setairportpower", "en0", "off"],
                capture_output=True,
                timeout=10,
                text=True
            )
            if result.returncode != 0:
                print(f"   Warning: {result.stderr}")
            else:
                print("   ✓ WiFi turned off")
        except Exception as e:
            print(f"   Error turning off WiFi: {e}")
        time.sleep(3)

        # Turn WiFi ON
        print("Step 2: 🔌 Turning WiFi on...")
        try:
            result = subprocess.run(
                ["networksetup", "-setairportpower", "en0", "on"],
                capture_output=True,
                timeout=10,
                text=True
            )
            if result.returncode != 0:
                print(f"   Warning: {result.stderr}")
            else:
                print("   ✓ WiFi turned on")
        except Exception as e:
            print(f"   Error turning on WiFi: {e}")
        time.sleep(5)

        # Connect
        print(f"Step 3: 🔗 Connecting to: {ssid}")
        try:
            result = subprocess.run(
                ["networksetup", "-setairportnetwork", "en0", ssid, password],
                capture_output=True,
                timeout=10,  # Reduced timeout
                text=True
            )
            if result.returncode != 0:
                stderr_msg = result.stderr.strip() if result.stderr else "No error message"
                print(f"   ⚠️  Connection command failed!")
                print(f"   Return code: {result.returncode}")
                print(f"   Error: {stderr_msg}")
                if "Could not find network" in stderr_msg or "not found" in stderr_msg:
                    print(f"   ❌ Network '{ssid}' not found!")
                    print(f"   Make sure WiFi is enabled on camera and SSID is correct")
            else:
                print(f"   ✓ Connection command executed successfully")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️  Connection command timed out after 10 seconds")
            print(f"   This might still succeed, continuing...")
        except Exception as e:
            print(f"   ❌ Error executing connect command: {e}")

        # Wait for connection to establish
        print("Step 4: ⏳ Waiting 12 seconds for connection to establish...")
        time.sleep(12)

        # Check what we're connected to
        current = self.get_current_wifi()
        print(f"Step 5: 📡 Checking current WiFi...")
        print(f"   Current SSID: {current}")
        print(f"   Target SSID: {ssid}")

        # Try to reach camera instead of strict SSID check (from working script)
        GOPRO_IP = "http://10.5.5.9:8080"
        print(f"Step 6: 🎯 Testing camera reachability at {GOPRO_IP}...")
        try:
            test_resp = requests.get(f"{GOPRO_IP}/gopro/media/list", timeout=5)
            if test_resp.status_code == 200:
                print(f"   ✅ SUCCESS! Camera is reachable (status: {test_resp.status_code})")
                print("=" * 60)
                return True
            else:
                print(f"   ⚠️  Camera responded but unexpected status: {test_resp.status_code}")
        except requests.exceptions.Timeout:
            print(f"   ❌ Timeout connecting to camera")
        except requests.exceptions.ConnectionError as e:
            print(f"   ❌ Connection error: {e}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

        # If camera not reachable but WiFi looks right, still try
        if current and (ssid.lower() in current.lower() or current.lower() in ssid.lower()):
            print(f"   ⚠️  WiFi SSID matches but camera not responding yet")
            print(f"   Waiting 5 more seconds...")
            time.sleep(5)
            print("   Trying anyway...")
            print("=" * 60)
            return True

        print("   ❌ FAILED: WiFi connection unsuccessful")
        print(f"   Expected: {ssid}")
        print(f"   Got: {current}")
        print("=" * 60)
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
