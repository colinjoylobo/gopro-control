"""
Shoot Manager for GoPro Multi-Camera App
Manages shoots (named filming sessions) and takes (record-to-stop cycles).
"""
import json
import uuid
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)

SHOOTS_FILE = Path(__file__).parent.parent.parent / "shoots.json"


class ShootManager:
    def __init__(self):
        self.shoots_file = SHOOTS_FILE
        self.data = self._load()

    def _load(self) -> dict:
        """Load shoots data from JSON file"""
        if self.shoots_file.exists():
            try:
                with open(self.shoots_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load shoots.json: {e}")
        return {"shoots": [], "active_shoot_id": None}

    def _save(self, data: Optional[dict] = None):
        """Save shoots data to JSON file"""
        if data is not None:
            self.data = data
        try:
            with open(self.shoots_file, 'w') as f:
                json.dump(self.data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save shoots.json: {e}")

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Replace characters that are invalid in filenames with underscores"""
        return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

    def create_shoot(self, name: str) -> dict:
        """Create a new shoot and set it as active"""
        shoot = {
            "id": str(uuid.uuid4()),
            "name": name,
            "created_at": datetime.now().isoformat(),
            "active": True,
            "current_take_number": 0,
            "takes": []
        }

        # Deactivate any currently active shoot
        for s in self.data["shoots"]:
            s["active"] = False

        self.data["shoots"].insert(0, shoot)
        self.data["active_shoot_id"] = shoot["id"]
        self._save()

        logger.info(f"Created shoot: {name} ({shoot['id']})")
        return shoot

    def list_shoots(self) -> List[dict]:
        """Return all shoots, newest first"""
        return sorted(
            self.data["shoots"],
            key=lambda s: s.get("created_at", ""),
            reverse=True
        )

    def get_active_shoot(self) -> Optional[dict]:
        """Return the currently active shoot, or None"""
        active_id = self.data.get("active_shoot_id")
        if not active_id:
            return None
        for shoot in self.data["shoots"]:
            if shoot["id"] == active_id:
                return shoot
        return None

    def set_active_shoot(self, shoot_id: str) -> Optional[dict]:
        """Activate a shoot by ID, deactivate others"""
        found = None
        for shoot in self.data["shoots"]:
            if shoot["id"] == shoot_id:
                shoot["active"] = True
                found = shoot
            else:
                shoot["active"] = False

        if found:
            self.data["active_shoot_id"] = shoot_id
            self._save()
            logger.info(f"Activated shoot: {found['name']} ({shoot_id})")
        return found

    def deactivate_shoot(self):
        """End the current shoot (set active_shoot_id to None)"""
        for shoot in self.data["shoots"]:
            shoot["active"] = False
        self.data["active_shoot_id"] = None
        self._save()
        logger.info("Deactivated shoot")

    def start_take(self, camera_serials: List[str]) -> Optional[dict]:
        """Start a new take on the active shoot. Returns the take dict or None if no active shoot."""
        active = self.get_active_shoot()
        if not active:
            return None

        active["current_take_number"] += 1
        take = {
            "take_number": active["current_take_number"],
            "started_at": datetime.now().isoformat(),
            "stopped_at": None,
            "cameras": camera_serials,
            "downloaded": False
        }
        active["takes"].append(take)
        self._save()

        logger.info(f"Started Take {take['take_number']} on shoot '{active['name']}' with cameras: {camera_serials}")
        return take

    def stop_take(self) -> Optional[dict]:
        """Stop the current take on the active shoot. Returns the take dict or None."""
        active = self.get_active_shoot()
        if not active or not active["takes"]:
            return None

        # Find the last take that hasn't been stopped yet
        for take in reversed(active["takes"]):
            if take.get("stopped_at") is None:
                take["stopped_at"] = datetime.now().isoformat()
                self._save()
                logger.info(f"Stopped Take {take['take_number']} on shoot '{active['name']}'")
                return take

        return None

    def delete_shoot(self, shoot_id: str) -> bool:
        """Remove a shoot by ID. Clears active if it was the active shoot."""
        original_len = len(self.data["shoots"])
        self.data["shoots"] = [s for s in self.data["shoots"] if s["id"] != shoot_id]

        if len(self.data["shoots"]) < original_len:
            if self.data.get("active_shoot_id") == shoot_id:
                self.data["active_shoot_id"] = None
            self._save()
            logger.info(f"Deleted shoot: {shoot_id}")
            return True
        return False

    def get_download_path(self, shoot_name: str, take_number: int, serial: str) -> str:
        """Return sanitized relative path: Shoot_Name/Take_01/GoPro8881"""
        safe_name = self._sanitize_filename(shoot_name)
        return f"{safe_name}/Take_{take_number:02d}/GoPro{serial}"
