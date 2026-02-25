"""
COHN (Camera on Home Network) Manager

Handles BLE provisioning of GoPro cameras to join a home WiFi network,
and manages stored credentials for HTTPS-based camera control.

Uses bleak directly (independent of open-gopro SDK) for BLE provisioning.
After provisioning, cameras are controlled via HTTPS with basic auth.
"""
import asyncio
import json
import logging
import os
import ssl
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Callable

import httpx
from bleak import BleakClient, BleakScanner

logger = logging.getLogger(__name__)

# BLE UUIDs for GoPro communication
CQ_COMMAND = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"
CQ_COMMAND_RESP = "b5f90073-aa8d-11e3-9046-0002a5d5c51b"
CQ_QUERY = "b5f90076-aa8d-11e3-9046-0002a5d5c51b"
CQ_QUERY_RESP = "b5f90077-aa8d-11e3-9046-0002a5d5c51b"
CM_NET_MGMT = "b5f90091-aa8d-11e3-9046-0002a5d5c51b"
CM_NET_MGMT_RESP = "b5f90092-aa8d-11e3-9046-0002a5d5c51b"

# Credentials file path
CREDENTIALS_FILE = Path(__file__).parent.parent.parent / "cohn_credentials.json"

# Temp cert directory
CERT_DIR = Path(tempfile.gettempdir()) / "gopro_cohn_certs"

# BLE debug logging (enable with GOPRO_BLE_DEBUG=1)
BLE_DEBUG = os.environ.get("GOPRO_BLE_DEBUG", "").strip() in ("1", "true", "yes")


class COHNManager:
    def __init__(self):
        self.credentials: Dict[str, dict] = {}
        self.wifi_ssid: Optional[str] = None
        self.wifi_password: Optional[str] = None
        self._notification_data: Dict[str, asyncio.Queue] = {}
        self._reassembly_buffers: Dict[str, dict] = {}
        self._provisioning_locks: Dict[str, asyncio.Lock] = {}
        self._load()

    # ============== Persistence ==============

    def _load(self):
        """Load credentials from cohn_credentials.json"""
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, 'r') as f:
                    data = json.load(f)
                self.wifi_ssid = data.get("wifi_ssid")
                self.wifi_password = data.get("wifi_password")
                self.credentials = data.get("cameras", {})
                logger.info(f"Loaded COHN credentials for {len(self.credentials)} camera(s)")
            except Exception as e:
                logger.error(f"Failed to load COHN credentials: {e}")
                self.credentials = {}
        else:
            logger.info("No COHN credentials file found")

    def _save(self):
        """Save credentials to cohn_credentials.json"""
        data = {
            "wifi_ssid": self.wifi_ssid,
            "wifi_password": self.wifi_password,
            "cameras": self.credentials
        }
        try:
            with open(CREDENTIALS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved COHN credentials for {len(self.credentials)} camera(s)")
        except Exception as e:
            logger.error(f"Failed to save COHN credentials: {e}")

    # ============== Hex Dump Logging (1b) ==============

    def _log_hex(self, direction: str, uuid_suffix: str, data: bytes):
        """Log BLE data as hex dump, gated by GOPRO_BLE_DEBUG env var"""
        if not BLE_DEBUG:
            return
        hex_str = data.hex() if data else "(empty)"
        readable = ' '.join(f'{b:02x}' for b in data) if data else "(empty)"
        logger.debug(f"BLE {direction} [{uuid_suffix}] ({len(data)}B): {readable}")

    # ============== Protobuf Helpers ==============

    def _encode_varint(self, value: int) -> bytes:
        """Encode an integer as a protobuf varint"""
        result = []
        while value > 0x7F:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

    def _decode_varint(self, data: bytes, offset: int = 0) -> tuple:
        """Decode a varint from bytes, return (value, new_offset).
        Raises ValueError on truncated or overlong varints (1c)."""
        result = 0
        shift = 0
        max_shift = 63  # 9 bytes max for 64-bit varint
        while offset < len(data):
            byte = data[offset]
            result |= (byte & 0x7F) << shift
            offset += 1
            if not (byte & 0x80):
                return result, offset
            shift += 7
            if shift > max_shift:
                raise ValueError(f"Varint too long (exceeded {max_shift} bits) at offset {offset}")
        raise ValueError(f"Truncated varint at offset {offset}, data length {len(data)}")

    def _encode_string_field(self, field_number: int, value: str) -> bytes:
        """Encode a protobuf string/bytes field"""
        encoded = value.encode('utf-8') if isinstance(value, str) else value
        tag = self._encode_varint((field_number << 3) | 2)  # wire type 2 = length-delimited
        length = self._encode_varint(len(encoded))
        return tag + length + encoded

    def _encode_bool_field(self, field_number: int, value: bool) -> bytes:
        """Encode a protobuf bool field"""
        tag = self._encode_varint((field_number << 3) | 0)  # wire type 0 = varint
        return tag + self._encode_varint(1 if value else 0)

    def _encode_int_field(self, field_number: int, value: int) -> bytes:
        """Encode a protobuf int32/enum field"""
        tag = self._encode_varint((field_number << 3) | 0)
        return tag + self._encode_varint(value)

    def _decode_protobuf_fields(self, data: bytes) -> dict:
        """Generic protobuf field parser - returns {field_number: value}"""
        fields = {}
        offset = 0
        while offset < len(data):
            if offset >= len(data):
                break
            tag, offset = self._decode_varint(data, offset)
            field_number = tag >> 3
            wire_type = tag & 0x07

            if wire_type == 0:  # varint
                value, offset = self._decode_varint(data, offset)
                fields[field_number] = value
            elif wire_type == 2:  # length-delimited
                length, offset = self._decode_varint(data, offset)
                value = data[offset:offset + length]
                fields[field_number] = value
                offset += length
            elif wire_type == 1:  # 64-bit
                value = data[offset:offset + 8]
                fields[field_number] = value
                offset += 8
            elif wire_type == 5:  # 32-bit
                value = data[offset:offset + 4]
                fields[field_number] = value
                offset += 4
            else:
                logger.warning(f"Unknown wire type {wire_type} for field {field_number}")
                break
        return fields

    # ============== BLE Helpers ==============

    def _build_protobuf_payload(self, feature_id: int, action_id: int, payload: bytes = b'') -> bytes:
        """Build the protobuf message body: [feature_id] [action_id] [payload]
        This does NOT include the BLE framing header — use _fragment_and_write for that."""
        return bytes([feature_id, action_id]) + payload

    def _fragment_payload(self, payload: bytes) -> list:
        """Fragment a payload into BLE packets with GoPro framing headers.

        GoPro BLE framing protocol:
        - Single packet (payload <= 31 bytes): [length_byte, payload...]
          where length_byte bits[6:5]=00, bits[4:0]=length
        - Multi-packet (payload > 18 bytes after header):
          First: 2-byte header [(length|0x2000)>>8, (length|0x2000)&0xFF, payload...]
          Continuation: [0x80, payload...]
        - Max BLE write size: 20 bytes per packet
        """
        MAX_PACKET_SIZE = 20
        length = len(payload)

        if length == 0:
            return [bytes([0x00])]

        # For small payloads that fit in a single packet (1-byte header + payload <= 20 bytes),
        # use the simple single-packet format: [length, payload...]
        if length <= 18:  # 1 header byte + 18 data bytes = 19 <= 20
            return [bytes([length]) + payload]

        # For larger payloads, use multi-packet fragmentation
        packets = []
        if length < (2**13 - 1):
            header = (length | 0x2000).to_bytes(2, "big")
        elif length < (2**16 - 1):
            header = (length | 0x6000).to_bytes(2, "big")
        else:
            raise ValueError(f"Payload too large: {length} bytes")

        byte_index = 0
        is_first = True

        while byte_index < length:
            if is_first:
                packet = bytearray(header)
                is_first = False
            else:
                packet = bytearray([0x80])  # Continuation header

            space = MAX_PACKET_SIZE - len(packet)
            chunk = payload[byte_index:byte_index + space]
            packet.extend(chunk)
            packets.append(bytes(packet))
            byte_index += len(chunk)

        return packets

    async def _fragment_and_write(self, client: BleakClient, write_uuid: str, payload: bytes):
        """Fragment payload into BLE packets and write each via GATT.
        Use this for any payload that might exceed 20 bytes (WiFi connect, etc.)."""
        packets = self._fragment_payload(payload)
        for i, packet in enumerate(packets):
            self._log_hex("TX-FRAG", write_uuid[-4:], packet)
            await client.write_gatt_char(write_uuid, packet, response=True)

    async def _write_and_wait(self, client: BleakClient, write_uuid: str,
                               notify_uuid: str, data: bytes, timeout: float = 30,
                               use_fragmentation: bool = False) -> bytes:
        """Write data to a characteristic and wait for notification response.
        Always uses BLE packet fragmentation (handles framing headers automatically)."""
        queue = self._notification_data.get(notify_uuid)
        if queue is None:
            queue = asyncio.Queue()
            self._notification_data[notify_uuid] = queue

        # Drain any stale notifications
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Always use fragmentation — it correctly handles BLE framing for any size
        await self._fragment_and_write(client, write_uuid, data)

        try:
            response = await asyncio.wait_for(queue.get(), timeout=timeout)
            self._log_hex("RX", notify_uuid[-4:], response)
            return response
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for response on {notify_uuid}")
            raise

    async def _wait_for_notification(self, notify_uuid: str, timeout: float = 30) -> bytes:
        """Wait for the next notification on a characteristic without writing anything."""
        queue = self._notification_data.get(notify_uuid)
        if queue is None:
            queue = asyncio.Queue()
            self._notification_data[notify_uuid] = queue
        try:
            response = await asyncio.wait_for(queue.get(), timeout=timeout)
            self._log_hex("RX-ASYNC", notify_uuid[-4:], response)
            return response
        except asyncio.TimeoutError:
            logger.debug(f"Timeout waiting for notification on {notify_uuid}")
            raise

    def _notification_handler(self, uuid: str):
        """Create a notification handler with multi-packet reassembly (1a).

        GoPro BLE framing protocol:
        - Byte 0 bits [6:5] = header type:
            00 = single packet (complete message)
            01 = first packet of multi-packet message
            10 = continuation packet
        - Single packet: bits [4:0] = payload length, data follows
        - First packet: next 1-2 bytes = total payload length, then data
        - Continuation: data follows the header byte
        """
        def handler(sender, data):
            raw = bytes(data)
            self._log_hex("NOTIFY", uuid[-4:], raw)

            if len(raw) == 0:
                return

            header = raw[0]

            # GoPro BLE framing protocol (correct bit interpretation):
            # Bit 7 = 1: Continuation packet (data follows from byte 1)
            # Bit 7 = 0, Bit 6 = 0: Single packet (bits[4:0] = length, data from byte 1)
            # Bit 7 = 0, Bit 6 = 1, Bit 5 = 0: First-of-multi, 13-bit length
            #   length = ((byte0 & 0x1F) << 8) | byte1, data from byte 2
            # Bit 7 = 0, Bit 6 = 1, Bit 5 = 1: First-of-multi, 16-bit length
            #   length = (byte1 << 8) | byte2, data from byte 3

            if header & 0x80:
                # Continuation packet (bit 7 set)
                buf = self._reassembly_buffers.get(uuid)
                if buf is None:
                    logger.warning(f"[BLE {uuid[-4:]}] Continuation without start, ignoring: {raw.hex()}")
                    return

                fragment = raw[1:]  # Skip header byte
                buf["data"].extend(fragment)
                logger.debug(f"[BLE {uuid[-4:]}] Continuation: {len(buf['data'])}/{buf['total_len']}B")

                if len(buf["data"]) >= buf["total_len"]:
                    complete_payload = bytes(buf["data"][:buf["total_len"]])
                    del self._reassembly_buffers[uuid]
                    logger.debug(f"[BLE {uuid[-4:]}] Reassembly complete: {len(complete_payload)}B")

                    queue = self._notification_data.get(uuid)
                    if queue:
                        queue.put_nowait(complete_payload)

            elif header & 0x60 == 0x60:
                # Extended first-of-multi (bits 6+5 set): 16-bit length in bytes 1-2
                if len(raw) < 3:
                    logger.warning(f"[BLE {uuid[-4:]}] Extended first-of-multi too short: {raw.hex()}")
                    return
                total_len = (raw[1] << 8) | raw[2]
                payload_start = 3
                fragment = raw[payload_start:]
                self._reassembly_buffers[uuid] = {
                    "total_len": total_len,
                    "data": bytearray(fragment)
                }
                logger.debug(f"[BLE {uuid[-4:]}] Multi-packet extended start: expecting {total_len}B, got {len(fragment)}B")

                if len(fragment) >= total_len:
                    complete_payload = bytes(fragment[:total_len])
                    del self._reassembly_buffers[uuid]
                    queue = self._notification_data.get(uuid)
                    if queue:
                        queue.put_nowait(complete_payload)

            elif header & 0x20:
                # First-of-multi (bit 5 set): 13-bit length in header+byte1
                # Length = ((byte0 & 0x1F) << 8) | byte1
                if len(raw) < 2:
                    logger.warning(f"[BLE {uuid[-4:]}] First-of-multi too short: {raw.hex()}")
                    return
                total_len = ((header & 0x1F) << 8) | raw[1]
                payload_start = 2
                fragment = raw[payload_start:]
                self._reassembly_buffers[uuid] = {
                    "total_len": total_len,
                    "data": bytearray(fragment)
                }
                logger.debug(f"[BLE {uuid[-4:]}] Multi-packet start: expecting {total_len}B, got {len(fragment)}B")

                if len(fragment) >= total_len:
                    complete_payload = bytes(fragment[:total_len])
                    del self._reassembly_buffers[uuid]
                    logger.debug(f"[BLE {uuid[-4:]}] Single-fragment complete: {len(complete_payload)}B")
                    queue = self._notification_data.get(uuid)
                    if queue:
                        queue.put_nowait(complete_payload)

            else:
                # Single packet (bits 7:5 = 000), length in bits[4:0]
                payload_len = header & 0x1F
                payload = raw[1:1 + payload_len]
                queue = self._notification_data.get(uuid)
                if queue:
                    queue.put_nowait(payload)

        return handler

    # ============== Response Parsing ==============

    def _parse_cohn_status_response(self, resp: bytes) -> Optional[dict]:
        """Parse a COHN status BLE response, handling both response formats.

        Response format: [feature_id] [action_id] [result_code?] [protobuf]
        The result_code byte may or may not be present. Try both offsets.
        Returns parsed protobuf fields dict or None if too short.
        """
        if len(resp) <= 2:
            return None

        # Try offset 3 first (feature + action + result_code), then offset 2
        for offset in (3, 2):
            if len(resp) <= offset:
                continue
            try:
                payload = resp[offset:]
                fields = self._decode_protobuf_fields(payload)
                # Sanity check: NotifyCOHNStatus should have field 1 (status enum)
                # If parsing at this offset produces reasonable fields, use it
                if fields and any(k in fields for k in (1, 2, 3, 4, 5, 6, 7, 8)):
                    return fields
            except (ValueError, IndexError):
                continue

        # Fallback: try offset 2
        try:
            return self._decode_protobuf_fields(resp[2:])
        except (ValueError, IndexError):
            return None

    # ============== Provisioning ==============

    async def provision_camera(self, serial: str, wifi_ssid: str, wifi_password: str,
                                progress_callback: Optional[Callable] = None):
        """
        Full COHN provisioning flow for one camera.
        Wrapped in a 5-minute overall timeout. Only one provisioning per camera at a time.
        """
        if serial not in self._provisioning_locks:
            self._provisioning_locks[serial] = asyncio.Lock()

        lock = self._provisioning_locks[serial]
        if lock.locked():
            raise Exception(f"Provisioning already in progress for camera {serial}")

        async with lock:
            try:
                return await asyncio.wait_for(
                    self._provision_camera_inner(serial, wifi_ssid, wifi_password, progress_callback),
                    timeout=300
                )
            except asyncio.TimeoutError:
                raise Exception(
                    f"COHN provisioning timed out after 5 minutes for camera {serial}. "
                    "Check that the camera is powered on, nearby, and not connected to another device."
                )

    async def _provision_camera_inner(self, serial: str, wifi_ssid: str, wifi_password: str,
                                       progress_callback: Optional[Callable] = None):
        """
        Inner provisioning flow (extracted for timeout wrapper).

        Steps:
        1. BLE scan for camera
        2. Connect via bleak
        3. Subscribe to notifications
        4. Set date/time
        5. Start WiFi AP scan
        6. Wait for scan results
        7. Connect camera to home WiFi
        8. Wait for connected state
        9. Clear existing COHN cert
        10. Create new COHN cert
        11. Get COHN cert
        12. Get COHN status (username, password, IP)
        13. Enable COHN
        14. Store credentials
        15. Disconnect BLE
        """
        self.wifi_ssid = wifi_ssid
        self.wifi_password = wifi_password

        async def report(step: int, total: int, msg: str):
            logger.info(f"[COHN {serial}] Step {step}/{total}: {msg}")
            if progress_callback:
                result = progress_callback(step, total, msg)
                if asyncio.iscoroutine(result):
                    await result

        total_steps = 15
        client = None

        try:
            # Step 1: BLE scan
            await report(1, total_steps, "Scanning for camera...")
            ble_name = f"GoPro {serial[-4:]}" if len(serial) > 4 else f"GoPro {serial}"
            logger.info(f"[COHN {serial}] Looking for BLE device: {ble_name}")

            device = None
            devices = await BleakScanner.discover(timeout=15)
            for d in devices:
                if d.name and ble_name in d.name:
                    device = d
                    logger.info(f"[COHN {serial}] Found: {d.name} ({d.address})")
                    break

            if not device:
                raise Exception(f"Camera not found via BLE. Make sure '{ble_name}' is powered on and nearby.")

            # Step 2: Connect
            await report(2, total_steps, "Connecting via BLE...")
            client = BleakClient(device.address)
            await client.connect(timeout=30)
            logger.info(f"[COHN {serial}] BLE connected to {device.address}")

            # Step 3: Subscribe to notifications
            await report(3, total_steps, "Setting up notifications...")

            # (1f) Initialize notification queues BEFORE subscribing
            for uuid in [CQ_COMMAND_RESP, CQ_QUERY_RESP, CM_NET_MGMT_RESP]:
                self._notification_data[uuid] = asyncio.Queue()
                # Clear any stale reassembly buffers
                self._reassembly_buffers.pop(uuid, None)

            await client.start_notify(CQ_COMMAND_RESP, self._notification_handler(CQ_COMMAND_RESP))
            await client.start_notify(CQ_QUERY_RESP, self._notification_handler(CQ_QUERY_RESP))
            await client.start_notify(CM_NET_MGMT_RESP, self._notification_handler(CM_NET_MGMT_RESP))
            await asyncio.sleep(1)

            # Step 4: Set date/time
            await report(4, total_steps, "Setting date/time...")
            now = datetime.now()
            # Command 0x0F = set date/time, payload: year(2) month(1) day(1) hour(1) min(1) sec(1)
            dt_payload = struct.pack('>HBBBBB',
                                     now.year, now.month, now.day,
                                     now.hour, now.minute, now.second)
            dt_packet = bytes([len(dt_payload) + 1, 0x0F]) + dt_payload
            try:
                self._log_hex("TX", "CMD", dt_packet)
                await client.write_gatt_char(CQ_COMMAND, dt_packet, response=True)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"[COHN {serial}] Set date/time warning: {e}")

            # Step 5: Start WiFi scan and wait for completion notification
            await report(5, total_steps, "Scanning for WiFi networks...")
            # Feature 0x02, Action 0x02 = StartScan (no payload)
            scan_body = self._build_protobuf_payload(0x02, 0x02)
            scan_id = 0
            try:
                # Send scan request (small packet, no fragmentation needed)
                scan_start_resp = await self._write_and_wait(
                    client, CM_NET_MGMT, CM_NET_MGMT_RESP, scan_body, timeout=15
                )
                logger.info(f"[COHN {serial}] Scan start response: {scan_start_resp.hex()}")

                # Now wait for scan COMPLETION notification (Action 0x0B = NotifStartScanning)
                # This is an async notification the camera sends when scanning is done
                scan_complete = False
                for _ in range(10):  # Wait up to ~30 seconds
                    try:
                        notif = await self._wait_for_notification(CM_NET_MGMT_RESP, timeout=5)
                        logger.info(f"[COHN {serial}] Scan notification: {notif.hex()}")
                        if len(notif) >= 2 and notif[0] == 0x02 and notif[1] == 0x0B:
                            # Parse NotifStartScanning protobuf
                            notif_payload = notif[2:]
                            notif_fields = self._decode_protobuf_fields(notif_payload)
                            logger.info(f"[COHN {serial}] Scan notif fields: {notif_fields}")
                            # Field 1 = scanning_state (enum), Field 2 = scan_id
                            scanning_state = notif_fields.get(1, 0)
                            scan_id = notif_fields.get(2, 0)
                            # EnumScanning: 0=UNKNOWN, 1=NEVER_STARTED, 2=STARTED, 3=ABORTED, 4=CANCELLED, 5=SUCCESS
                            if scanning_state == 5:  # SCANNING_SUCCESS
                                logger.info(f"[COHN {serial}] Scan complete! scan_id={scan_id}")
                                scan_complete = True
                                break
                    except asyncio.TimeoutError:
                        continue

                if not scan_complete:
                    logger.warning(f"[COHN {serial}] Scan completion not confirmed, proceeding with scan_id={scan_id}")

            except Exception as e:
                logger.warning(f"[COHN {serial}] Scan start error: {e}, proceeding anyway")

            # Step 6: Get scan results using scan_id
            await report(6, total_steps, "Getting WiFi scan results...")
            get_entries_payload = (
                self._encode_int_field(1, 0) +        # start_index
                self._encode_int_field(2, 100) +       # max_entries
                self._encode_int_field(3, scan_id)     # scan_id
            )
            get_entries_body = self._build_protobuf_payload(0x02, 0x03, get_entries_payload)
            found_target_ssid = False
            try:
                scan_resp = await self._write_and_wait(
                    client, CM_NET_MGMT, CM_NET_MGMT_RESP, get_entries_body, timeout=15
                )
                logger.info(f"[COHN {serial}] Scan results: {len(scan_resp)}B raw={scan_resp.hex()}")
                if len(scan_resp) > 2:
                    scan_payload = scan_resp[2:]
                    scan_fields = self._decode_protobuf_fields(scan_payload)
                    logger.info(f"[COHN {serial}] Scan result fields: {{{', '.join(f'{k}: {v.hex() if isinstance(v, bytes) else v}' for k, v in scan_fields.items())}}}")
                    # Look for entries (field 3 = repeated ScanEntry)
                    for fnum, fval in scan_fields.items():
                        if isinstance(fval, bytes):
                            decoded = fval.decode('utf-8', errors='ignore')
                            if decoded.isprintable() and len(decoded) > 1:
                                logger.info(f"[COHN {serial}] Scan entry field {fnum}: '{decoded}'")
                                if wifi_ssid in decoded:
                                    found_target_ssid = True
            except Exception as e:
                logger.warning(f"[COHN {serial}] Get scan results error: {e}")

            if not found_target_ssid:
                logger.warning(f"[COHN {serial}] Target SSID '{wifi_ssid}' may not be visible to camera")

            # Step 7: Connect to home WiFi (using fragmented BLE write for large payloads)
            await report(7, total_steps, f"Connecting camera to {wifi_ssid}...")
            # Feature 0x02, Action 0x05 = RequestConnectNew
            connect_protobuf = (
                self._encode_string_field(1, wifi_ssid) +
                self._encode_string_field(2, wifi_password)
            )
            connect_body = self._build_protobuf_payload(0x02, 0x05, connect_protobuf)
            wifi_connected = False
            try:
                # Use fragmented write — payload is likely > 20 bytes
                connect_resp = await self._write_and_wait(
                    client, CM_NET_MGMT, CM_NET_MGMT_RESP, connect_body,
                    timeout=30, use_fragmentation=True
                )
                logger.info(f"[COHN {serial}] WiFi connect response: {connect_resp.hex()}")

                # Wait for provisioning state notifications (Action 0x0C = NotifProvisioningState)
                for _ in range(20):  # Wait up to ~60 seconds
                    try:
                        notif = await self._wait_for_notification(CM_NET_MGMT_RESP, timeout=5)
                        logger.info(f"[COHN {serial}] Provisioning notification: {notif.hex()}")
                        if len(notif) >= 2 and notif[0] == 0x02 and notif[1] == 0x0C:
                            prov_payload = notif[2:]
                            prov_fields = self._decode_protobuf_fields(prov_payload)
                            prov_state = prov_fields.get(1, -1)
                            logger.info(f"[COHN {serial}] Provisioning state: {prov_state}")
                            # EnumProvisioning: 0=STARTED, 1=NOT_STARTED, 2=ABORTED_REMAIN_ON,
                            # 3=ABORTED_REVERT_PREVIOUS, 4=ERROR, 5=SUCCESS_NEW_AP, 6=SUCCESS_OLD_AP
                            if prov_state in (5, 6):  # SUCCESS_NEW_AP or SUCCESS_OLD_AP
                                logger.info(f"[COHN {serial}] WiFi provisioning SUCCESS!")
                                wifi_connected = True
                                break
                            elif prov_state in (2, 3, 4):  # ABORTED or ERROR
                                logger.error(f"[COHN {serial}] WiFi provisioning FAILED: state={prov_state}")
                                break
                    except asyncio.TimeoutError:
                        continue

            except asyncio.TimeoutError:
                logger.warning(f"[COHN {serial}] WiFi connect command timed out, continuing to poll status...")
            except Exception as e:
                logger.warning(f"[COHN {serial}] WiFi connect warning: {e}, continuing to poll status...")

            # Step 8: Wait for network connected state
            await report(8, total_steps, "Waiting for WiFi connection...")
            # Poll COHN status to check if network is connected
            # Feature 0xF5, Action 0x6F = GetCOHNStatus
            # IMPORTANT: Must include register_cohn_status=true (field 1) or camera returns empty
            connected = False
            discovered_ip = ""
            for attempt in range(30):  # Up to 90 seconds (30 * 3s)
                await asyncio.sleep(3)
                try:
                    register_payload = self._encode_bool_field(1, True)  # register_cohn_status = true
                    status_packet = self._build_protobuf_payload(0xF5, 0x6F, register_payload)
                    status_resp = await self._write_and_wait(client, CQ_QUERY, CQ_QUERY_RESP,
                                                              status_packet, timeout=10)
                    logger.info(f"[COHN {serial}] Status poll attempt {attempt}: {len(status_resp)}B raw={status_resp.hex()}")
                    # Parse response - try offset 2 (feature+action) and offset 3 (feature+action+result)
                    fields = self._parse_cohn_status_response(status_resp)
                    if fields:
                        logger.info(f"[COHN {serial}] Status poll fields: {{{', '.join(f'{k}: {v.hex() if isinstance(v, bytes) else v}' for k, v in fields.items())}}}")
                        # NotifyCOHNStatus fields:
                        # 1=status(enum), 2=state(enum), 3=username, 4=password,
                        # 5=ipaddress, 6=enabled, 7=ssid, 8=macaddress
                        # EnumCOHNNetworkState: 0=Init, 1=Error, 2=Exit, 5=Idle,
                        #   27=NetworkConnected, 28=NetworkDisconnected, 29=ConnectingToNetwork, 30=Invalid
                        state_val = fields.get(2, -1)
                        state_names = {0:'Init', 1:'Error', 2:'Exit', 5:'Idle',
                                       27:'NetworkConnected', 28:'NetworkDisconnected',
                                       29:'ConnectingToNetwork', 30:'Invalid'}
                        state_name = state_names.get(state_val, f'Unknown({state_val})')
                        logger.info(f"[COHN {serial}] COHN state: {state_name} ({state_val}), status: {fields.get(1, '?')}")

                        ip_bytes = fields.get(5, b'')
                        if isinstance(ip_bytes, bytes) and len(ip_bytes) > 0:
                            ip_str = ip_bytes.decode('utf-8', errors='ignore')
                            if ip_str and '.' in ip_str:
                                logger.info(f"[COHN {serial}] Camera got IP: {ip_str}")
                                discovered_ip = ip_str
                                connected = True
                        if state_val == 27:  # COHN_STATE_NetworkConnected
                            connected = True
                        if connected:
                            break
                except Exception as e:
                    logger.info(f"[COHN {serial}] Status poll attempt {attempt} error: {e}")

            if not connected:
                # Also drain any async notifications that arrived
                query_queue = self._notification_data.get(CQ_QUERY_RESP)
                if query_queue:
                    while not query_queue.empty():
                        try:
                            async_resp = query_queue.get_nowait()
                            logger.info(f"[COHN {serial}] Async notification: {len(async_resp)}B raw={async_resp.hex()}")
                            fields = self._parse_cohn_status_response(async_resp)
                            if fields:
                                ip_bytes = fields.get(5, b'')
                                if isinstance(ip_bytes, bytes):
                                    ip_str = ip_bytes.decode('utf-8', errors='ignore')
                                    if ip_str and '.' in ip_str:
                                        discovered_ip = ip_str
                                        connected = True
                        except asyncio.QueueEmpty:
                            break

            if not connected:
                logger.info(f"[COHN {serial}] Waiting additional time for WiFi connection...")
                await asyncio.sleep(15)

            # Step 9: Clear old COHN cert
            await report(9, total_steps, "Clearing old COHN certificate...")
            # Feature 0xF1, Action 0x66 = ClearCOHNCert
            clear_packet = self._build_protobuf_payload(0xF1, 0x66)
            try:
                await self._write_and_wait(client, CQ_COMMAND, CQ_COMMAND_RESP,
                                            clear_packet, timeout=15)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[COHN {serial}] Clear cert warning (may not exist): {e}")

            # Step 10: Create new COHN cert
            await report(10, total_steps, "Creating new COHN certificate...")
            # Feature 0xF1, Action 0x67 = CreateCOHNCert
            # Protobuf: field 1 = override (bool, true)
            create_payload = self._encode_bool_field(1, True)
            create_packet = self._build_protobuf_payload(0xF1, 0x67, create_payload)
            await self._write_and_wait(client, CQ_COMMAND, CQ_COMMAND_RESP,
                                        create_packet, timeout=30)
            await asyncio.sleep(3)

            # Step 11: Get COHN cert
            await report(11, total_steps, "Retrieving COHN certificate...")
            # Feature 0xF5, Action 0x6E = GetCOHNCert
            cert_packet = self._build_protobuf_payload(0xF5, 0x6E)
            cert_resp = await self._write_and_wait(client, CQ_QUERY, CQ_QUERY_RESP,
                                                    cert_packet, timeout=15)
            certificate = ""
            if len(cert_resp) > 2:
                # ResponseCOHNCert: field 1 = result (enum), field 2 = cert (string)
                # Try offset 3 (feature+action+result) and offset 2 (feature+action)
                for cert_offset in (3, 2):
                    if len(cert_resp) > cert_offset:
                        try:
                            cert_fields = self._decode_protobuf_fields(cert_resp[cert_offset:])
                            # Field 2 = cert PEM (field 1 is result code in ResponseCOHNCert)
                            cert_bytes = cert_fields.get(2, b'') or cert_fields.get(1, b'')
                            if isinstance(cert_bytes, bytes) and b'BEGIN' in cert_bytes:
                                certificate = cert_bytes.decode('utf-8', errors='ignore')
                                logger.info(f"[COHN {serial}] Got certificate ({len(certificate)} chars)")
                                break
                        except (ValueError, IndexError):
                            continue

            # Step 12: Get COHN status (username, password, IP)
            await report(12, total_steps, "Getting COHN credentials...")
            register_payload = self._encode_bool_field(1, True)  # register_cohn_status = true
            status_packet = self._build_protobuf_payload(0xF5, 0x6F, register_payload)
            status_resp = await self._write_and_wait(client, CQ_QUERY, CQ_QUERY_RESP,
                                                      status_packet, timeout=15)

            username = "gopro"
            password = ""
            ip_address = ""
            mac_address = ""

            logger.info(f"[COHN {serial}] Step 12 raw response ({len(status_resp)}B): {status_resp.hex()}")
            status_fields = self._parse_cohn_status_response(status_resp)
            if status_fields:
                logger.info(f"[COHN {serial}] Step 12 parsed fields: {{{', '.join(f'{k}: {v.hex() if isinstance(v, bytes) else v}' for k, v in status_fields.items())}}}")

                # NotifyCOHNStatus fields (from GoPro protobuf spec):
                # Field 1 = status (EnumCOHNStatus)
                # Field 2 = state (EnumCOHNNetworkState)
                # Field 3 = username (string)
                # Field 4 = password (string)
                # Field 5 = ipaddress (string)
                # Field 6 = enabled (bool)
                # Field 7 = ssid (string)
                # Field 8 = macaddress (string)
                for field_num, value in status_fields.items():
                    if isinstance(value, bytes):
                        decoded = value.decode('utf-8', errors='ignore')
                    else:
                        decoded = value

                    if field_num == 3 and isinstance(value, bytes):
                        username = decoded
                    elif field_num == 4 and isinstance(value, bytes):
                        password = decoded
                    elif field_num == 5 and isinstance(value, bytes):
                        ip_address = decoded
                    elif field_num == 8 and isinstance(value, bytes):
                        mac_address = decoded

                logger.info(f"[COHN {serial}] COHN Status - IP: {ip_address}, User: {username}, MAC: {mac_address}")

            # Use discovered_ip from step 8 if step 12 didn't return one
            if not ip_address and discovered_ip:
                ip_address = discovered_ip
                logger.info(f"[COHN {serial}] Using IP discovered in step 8: {ip_address}")

            # If still no IP, try ARP/network scan to find camera
            if not ip_address or '.' not in ip_address:
                logger.info(f"[COHN {serial}] No IP from BLE, trying network scan...")
                try:
                    import subprocess
                    # Use arp -a to find GoPro on the network
                    arp_output = subprocess.check_output(['arp', '-a'], text=True, timeout=5)
                    if mac_address:
                        mac_lower = mac_address.lower().replace(':', '')
                        for line in arp_output.split('\n'):
                            line_mac = ''.join(c for c in line.split('at')[-1].split('on')[0] if c in '0123456789abcdef').lower() if 'at' in line else ''
                            if mac_lower and mac_lower in line_mac:
                                # Extract IP from arp line: ? (192.168.x.x) at ...
                                parts = line.split('(')
                                if len(parts) > 1:
                                    ip_candidate = parts[1].split(')')[0]
                                    if '.' in ip_candidate:
                                        ip_address = ip_candidate
                                        logger.info(f"[COHN {serial}] Found IP via ARP: {ip_address}")
                except Exception as e:
                    logger.warning(f"[COHN {serial}] Network scan failed: {e}")

            # Validate IP and password before storing
            if not ip_address or '.' not in ip_address:
                raise Exception(
                    f"Camera did not return a valid IP address (got: '{ip_address}'). "
                    "The camera may not have connected to WiFi successfully. "
                    "Check WiFi credentials and try again."
                )
            if not password:
                logger.warning(f"[COHN {serial}] No COHN password returned, using default")
                password = "gopro_cohn"  # Fallback - may not work but lets provisioning complete

            # Step 13: Enable COHN
            await report(13, total_steps, "Enabling COHN...")
            # Feature 0xF1, Action 0x65 = SetCOHNSetting
            # Protobuf: field 1 = cohn_active (bool, true)
            enable_payload = self._encode_bool_field(1, True)
            enable_packet = self._build_protobuf_payload(0xF1, 0x65, enable_payload)
            try:
                await self._write_and_wait(client, CQ_COMMAND, CQ_COMMAND_RESP,
                                            enable_packet, timeout=15)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"[COHN {serial}] Enable COHN warning: {e}")

            # Step 14: Store credentials
            await report(14, total_steps, "Storing credentials...")
            self.credentials[serial] = {
                "ip_address": ip_address,
                "username": username,
                "password": password,
                "certificate": certificate,
                "provisioned_at": datetime.now().isoformat(),
                "mac_address": mac_address
            }
            self._save()
            logger.info(f"[COHN {serial}] Credentials saved")

            # Step 15: Disconnect BLE
            await report(15, total_steps, "Provisioning complete!")
            await client.disconnect()
            logger.info(f"[COHN {serial}] BLE disconnected, provisioning complete")

            return {
                "success": True,
                "serial": serial,
                "ip_address": ip_address,
                "username": username,
                "mac_address": mac_address
            }

        except Exception as e:
            logger.error(f"[COHN {serial}] Provisioning failed: {e}", exc_info=True)
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            raise

    # ============== Credential Access ==============

    def get_credentials(self, serial: str) -> Optional[dict]:
        """Return {ip, username, password, cert} or None"""
        return self.credentials.get(serial)

    def get_all_credentials(self) -> Dict[str, dict]:
        """All provisioned cameras"""
        return self.credentials.copy()

    def is_provisioned(self, serial: str) -> bool:
        """Quick check if camera has COHN credentials"""
        return serial in self.credentials

    def remove_credentials(self, serial: str) -> bool:
        """Remove COHN credentials for a camera"""
        if serial in self.credentials:
            del self.credentials[serial]
            self._save()
            return True
        return False

    def get_auth_header(self, serial: str) -> Optional[str]:
        """Return Basic auth header value"""
        creds = self.credentials.get(serial)
        if not creds:
            return None
        import base64
        auth = base64.b64encode(
            f"{creds['username']}:{creds['password']}".encode()
        ).decode()
        return f"Basic {auth}"

    def get_https_base_url(self, serial: str) -> Optional[str]:
        """Return https://{ip}"""
        creds = self.credentials.get(serial)
        if not creds or not creds.get('ip_address'):
            return None
        return f"https://{creds['ip_address']}"

    # ============== COHN Status (HTTPS) ==============

    def _write_temp_cert(self, serial: str, certificate: str) -> Optional[str]:
        """Write certificate to temp file for SSL verification"""
        if not certificate:
            return None
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        cert_path = CERT_DIR / f"gopro_{serial}.pem"
        cert_path.write_text(certificate)
        return str(cert_path)

    async def check_camera_online(self, serial: str) -> bool:
        """Check if a COHN-provisioned camera is reachable via HTTPS"""
        creds = self.credentials.get(serial)
        if not creds or not creds.get('ip_address'):
            return False

        try:
            base_url = f"https://{creds['ip_address']}"
            auth_header = self.get_auth_header(serial)

            async with httpx.AsyncClient(verify=False, timeout=5.0) as client:
                resp = await client.get(
                    f"{base_url}/gopro/camera/state",
                    headers={"Authorization": auth_header} if auth_header else {}
                )
                return resp.status_code == 200
        except Exception as e:
            logger.debug(f"[COHN {serial}] Online check failed: {e}")
            return False

    async def check_all_cameras(self) -> Dict[str, bool]:
        """Check all provisioned cameras"""
        results = {}
        tasks = []
        serials = []

        for serial in self.credentials:
            tasks.append(self.check_camera_online(serial))
            serials.append(serial)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for serial, result in zip(serials, task_results):
                results[serial] = result if isinstance(result, bool) else False

        return results
