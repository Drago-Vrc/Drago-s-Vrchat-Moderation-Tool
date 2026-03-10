import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from .printing import safe_print


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in _TRUE_ENV_VALUES:
        return True
    if value in _FALSE_ENV_VALUES:
        return False
    return default


class Config:
    PROCESS_NAME = "VRChat.exe"
    SCAN_INTERVAL = 1.0
    STATUS_INTERVAL = 15.0

    # blank by default; GUI/env sets it
    DEFAULT_DISCORD_WEBHOOK = ""
    DISCORD_WEBHOOK = ""

    OUTPUT_DIR = Path(".").resolve()
    PLAYERS_FILE = OUTPUT_DIR / "players.txt"
    SESSION_LOG = OUTPUT_DIR / "session_history.log"
    SETTINGS_FILE = OUTPUT_DIR / "moderation_tool_settings.json"

    VRCHAT_LOG_DIR: Optional[Path] = None
    VRCX_DB_FILE: Optional[Path] = None

    @staticmethod
    def get_output_dir() -> Path:
        try:
            if getattr(sys, "frozen", False):
                return Path(sys.executable).resolve().parent

            here = Path(__file__).resolve()
            # source tree path
            if here.parent.name == "kryzln_vrc_logger" and here.parent.parent.name == "src":
                return here.parent.parent.parent
            return here.parent
        except (OSError, RuntimeError, ValueError):
            return Path(".").resolve()

    @staticmethod
    def get_vrchat_log_dir() -> Path:
        local_app = os.getenv("LOCALAPPDATA", "")
        if local_app:
            return Path(local_app).parent / "LocalLow" / "VRChat" / "VRChat"
        return Path.home() / "AppData" / "LocalLow" / "VRChat" / "VRChat"

    @staticmethod
    def get_vrcx_db_file() -> Path:
        app_data = os.getenv("APPDATA", "")
        if app_data:
            return Path(app_data) / "VRCX" / "VRCX.sqlite3"
        return Path.home() / "AppData" / "Roaming" / "VRCX" / "VRCX.sqlite3"

    @classmethod
    def _load_settings(cls) -> Dict[str, str]:
        settings: Dict[str, str] = {}
        try:
            path = cls.SETTINGS_FILE
            if not path.exists():
                return settings
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return settings
            if "discord_webhook" in raw:
                settings["discord_webhook"] = str(raw.get("discord_webhook") or "").strip()
                settings["discord_webhook_set"] = "1"
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return settings
        return settings

    @classmethod
    def set_discord_webhook(cls, webhook_url: str) -> bool:
        value = (webhook_url or "").strip()
        payload = {"discord_webhook": value}
        try:
            cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            cls.SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            cls.DISCORD_WEBHOOK = value
            return True
        except OSError as exc:
            safe_print(f"[!] Failed to save webhook settings: {exc}")
            return False

    @classmethod
    def init(cls):
        cls.OUTPUT_DIR = cls.get_output_dir()
        cls.PLAYERS_FILE = cls.OUTPUT_DIR / "players.txt"
        cls.SESSION_LOG = cls.OUTPUT_DIR / "session_history.log"
        cls.SETTINGS_FILE = cls.OUTPUT_DIR / "moderation_tool_settings.json"

        env_webhook = os.getenv("KRYZLN_DISCORD_WEBHOOK", "").strip()
        settings = cls._load_settings()
        if env_webhook:
            cls.DISCORD_WEBHOOK = env_webhook
        elif settings.get("discord_webhook_set") == "1":
            # keep blank value so "clear webhook" sticks
            cls.DISCORD_WEBHOOK = settings.get("discord_webhook", "")
        else:
            cls.DISCORD_WEBHOOK = cls.DEFAULT_DISCORD_WEBHOOK
        cls.VRCHAT_LOG_DIR = cls.get_vrchat_log_dir()
        cls.VRCX_DB_FILE = cls.get_vrcx_db_file()


