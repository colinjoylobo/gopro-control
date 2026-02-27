"""
Preset Manager — Save/Load/Push camera settings presets
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PresetManager:
    PRESETS_FILE = Path(__file__).parent.parent / "camera_presets.json"

    def __init__(self):
        self.presets: Dict[str, dict] = {}
        self._load()

    def _load(self):
        """Load presets from JSON file"""
        if self.PRESETS_FILE.exists():
            try:
                with open(self.PRESETS_FILE, 'r') as f:
                    self.presets = json.load(f)
                logger.info(f"Loaded {len(self.presets)} preset(s) from {self.PRESETS_FILE}")
            except Exception as e:
                logger.error(f"Failed to load presets: {e}")
                self.presets = {}
        else:
            logger.info(f"No presets file found at {self.PRESETS_FILE}")
            self.presets = {}

    def _save(self):
        """Persist presets to JSON file"""
        try:
            with open(self.PRESETS_FILE, 'w') as f:
                json.dump(self.presets, f, indent=2)
            logger.info(f"Saved {len(self.presets)} preset(s) to {self.PRESETS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save presets: {e}")

    def save_preset(self, name: str, settings: dict):
        """Create or update a preset. Preserves existing pinned status on update."""
        existing = self.presets.get(name, {})
        pinned = existing.get("pinned", False)
        self.presets[name] = {
            **settings,
            "created_at": datetime.now().isoformat(),
            "pinned": pinned,
        }
        self._save()
        logger.info(f"Saved preset: {name}")
        return self.presets[name]

    def get_preset(self, name: str) -> Optional[dict]:
        """Get a single preset by name"""
        return self.presets.get(name)

    def list_presets(self) -> Dict[str, dict]:
        """List all presets, pinned first"""
        # Sort: pinned first, then alphabetical
        sorted_names = sorted(
            self.presets.keys(),
            key=lambda n: (not self.presets[n].get("pinned", False), n.lower())
        )
        return {name: self.presets[name] for name in sorted_names}

    def toggle_pin(self, name: str) -> Optional[bool]:
        """Toggle pinned status. Returns new pinned value, or None if preset not found."""
        preset = self.presets.get(name)
        if not preset:
            return None
        new_val = not preset.get("pinned", False)
        preset["pinned"] = new_val
        self._save()
        logger.info(f"Preset '{name}' pinned={new_val}")
        return new_val

    def delete_preset(self, name: str) -> bool:
        """Delete a preset by name"""
        if name in self.presets:
            del self.presets[name]
            self._save()
            logger.info(f"Deleted preset: {name}")
            return True
        return False
