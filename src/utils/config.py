"""
Configuration Manager for SimLaps Client.

Handles persistent settings stored in AppData.
"""

import os
import json
import shutil
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from urllib.parse import urlparse

from src.utils.structured_logger import Component, log_warning
from src.core.security import is_secret_configured


# Default configuration values
DEFAULT_LOG_PATH = str(Path.home() / "Saved Games" / "ACE" / "Logs")
DEFAULT_SERVER_URL = "https://simlaps.racing"
APP_NAME = "SimLapsClient"

# Config version for schema migration support.
# Increment when fields are renamed, removed, or have breaking changes.
CONFIG_VERSION = 1

# Keep persisted values within ranges that the UI and history views can use
# safely.  The upper bounds are deliberately generous so multi-monitor
# layouts and long histories remain supported without allowing pathological
# values from a hand-edited JSON file to reach the UI.
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 150
MAX_WINDOW_DIMENSION = 10_000
MIN_HISTORY_ITEMS = 1
MAX_HISTORY_ITEMS = 10_000

# Maps legacy (old) field names to their current replacements.
# When a config dict contains a legacy key, it is transparently renamed
# to the current key during load. Remove entries once the oldest supported
# config version no longer emits them.
_LEGACY_FIELD_MAP: dict[str, str] = {
    # Example: "discord_post_invalid": "submit_invalid_laps",
}


def get_config_dir() -> Path:
    """Get the configuration directory path."""
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    else:  # macOS/Linux
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    
    config_dir = Path(base) / APP_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    """Get the configuration file path.

    When APP_SECRET is not configured (debug mode), a separate
    ``config-debug.json`` file is used so debug settings don't
    overwrite the production config.
    """
    filename = "config-debug.json" if not is_secret_configured() else "config.json"
    return get_config_dir() / filename


@dataclass
class AppConfig:
    """Application configuration settings."""
    
    # Schema version — incremented when fields are added/renamed/removed.
    # Persisted in the JSON file so from_dict() can run migrations.
    config_version: int = CONFIG_VERSION
    
    # Paths
    log_path: str = field(default_factory=lambda: DEFAULT_LOG_PATH)
    
    # Server
    server_url: str = DEFAULT_SERVER_URL
    
    # Behavior
    auto_submit: bool = True
    submit_invalid_laps: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    start_with_windows: bool = False
    
    # UI
    theme: str = "dark"
    window_width: int = 500
    window_height: int = 700
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    
    # History
    max_history_items: int = 100
    
    # Discord Integration
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool = False
    discord_pb_only: bool = True

    # Telemetry
    telemetry_enabled: bool = False
    telemetry_output_path: str = field(default_factory=lambda: str(Path.home() / "Documents" / "SimLaps" / "Telemetry"))
    # When False (default), suppress on-disk debug artefacts produced by the
    # telemetry capture: ``telemetry_diagnostics_*.log``, ``capture_*.jsonl``,
    # and ``raw_dump_*.jsonl``. The summary HTML / AI prompt / analyzer
    # outputs are unaffected. Toggle this on only when reverse-engineering
    # SHM layouts or chasing a capture-loop bug.
    telemetry_debug_logs: bool = False
    
    def to_dict(self) -> dict:
        """Convert config to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Any) -> "AppConfig":
        """Create config from dictionary.
        
        Applies schema migrations for legacy field renames and logs warnings
        when unrecognised fields are encountered so they can be cleaned up
        in a future config version.
        """
        # JSON permits scalar and list top-level values.  They are not valid
        # configuration documents, however, and must not be passed to
        # ``dict()`` (which can raise or produce surprising mappings).
        if not isinstance(data, dict):
            log_warning(Component.CONFIG, "Ignoring config with invalid top-level type")
            return cls()

        data = dict(data)  # shallow copy so we don't mutate the caller's dict
        
        # --- Step 1: Apply legacy field renames ---
        for old_key, new_key in _LEGACY_FIELD_MAP.items():
            if old_key in data and new_key not in data:
                log_warning(
                    Component.CONFIG,
                    "Migrating legacy config field",
                    old=old_key,
                    new=new_key,
                )
                data[new_key] = data.pop(old_key)
        
        # --- Step 2: Collect unknown / legacy fields for warning ---
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        legacy_fields = [k for k in data if k not in valid_fields]
        if legacy_fields:
            log_warning(
                Component.CONFIG,
                "Ignoring unknown config field(s) — consider cleaning up old config file",
                fields=legacy_fields,
            )
        
        # --- Step 3: Validate and filter fields ---
        filtered: dict[str, Any] = {}
        invalid_fields: list[str] = []
        for key in valid_fields:
            if key not in data:
                continue
            value = data[key]
            if _is_valid_config_value(key, value):
                filtered[key] = value
            else:
                invalid_fields.append(key)

        if invalid_fields:
            log_warning(
                Component.CONFIG,
                "Using defaults for invalid config field(s)",
                fields=sorted(invalid_fields),
            )

        # --- Step 4: Stamp current version so the next load is clean ---
        filtered["config_version"] = CONFIG_VERSION
        
        return cls(**filtered)


def _is_valid_url(value: Any) -> bool:
    """Return whether *value* is a usable HTTP(S) URL without logging it."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_valid_config_value(field_name: str, value: Any) -> bool:
    """Validate one persisted field, rejecting bools where ints are expected."""
    if field_name == "config_version":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if field_name in {"log_path", "telemetry_output_path"}:
        return isinstance(value, str) and bool(value.strip())
    if field_name == "server_url":
        return _is_valid_url(value)
    if field_name == "discord_webhook_url":
        return value is None or _is_valid_url(value)
    if field_name == "theme":
        return isinstance(value, str) and value in {"dark", "light"}
    if field_name in {
        "auto_submit",
        "submit_invalid_laps",
        "minimize_to_tray",
        "start_minimized",
        "start_with_windows",
        "discord_enabled",
        "discord_pb_only",
        "telemetry_enabled",
        "telemetry_debug_logs",
    }:
        return type(value) is bool
    if field_name == "window_width":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_WINDOW_WIDTH <= value <= MAX_WINDOW_DIMENSION
        )
    if field_name == "window_height":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_WINDOW_HEIGHT <= value <= MAX_WINDOW_DIMENSION
        )
    if field_name in {"window_x", "window_y"}:
        return value is None or (
            isinstance(value, int) and not isinstance(value, bool)
        )
    if field_name == "max_history_items":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_HISTORY_ITEMS <= value <= MAX_HISTORY_ITEMS
        )
    # This is defensive for future fields: unknown fields are already
    # filtered, while a newly added field should opt into explicit validation.
    return False


def _invalid_config_fields(data: Any) -> list[str]:
    """Return persisted known fields that fail schema validation."""
    if not isinstance(data, dict):
        return ["<top-level>"]
    # Apply the same legacy rename semantics as ``from_dict`` so an invalid
    # value carried by a supported legacy key is also backed up before repair.
    data = dict(data)
    for old_key, new_key in _LEGACY_FIELD_MAP.items():
        if old_key in data and new_key not in data:
            data[new_key] = data[old_key]
    valid_fields = {f.name for f in AppConfig.__dataclass_fields__.values()}
    return sorted(
        key
        for key, value in data.items()
        if key in valid_fields and not _is_valid_config_value(key, value)
    )
    

class ConfigManager:
    """
    Manages application configuration.
    
    Handles loading, saving, and updating configuration values.
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Optional custom config file path
        """
        self.config_path = config_path or get_config_path()
        self._config: Optional[AppConfig] = None
        self._loaded = False
    
    def load(self) -> AppConfig:
        """
        Load configuration from file.
        
        Creates default config if file doesn't exist.
        Migrates old ACE log path to new location.
        
        Returns:
            Loaded or default configuration
        """
        if self._loaded and self._config:
            return self._config
        
        needs_repair = False
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config = AppConfig.from_dict(data)
                needs_repair = bool(_invalid_config_fields(data))
            except (json.JSONDecodeError, UnicodeError, OSError) as e:
                log_warning(Component.CONFIG, f"Error loading config: {e}")
                self._config = AppConfig()
            else:
                if needs_repair:
                    self._backup_before_repair()
                    self.save()
        else:
            self._config = AppConfig()
        
        # Migrate old ACE log path (log.txt → Logs directory)
        old_log_path = str(Path.home() / "Saved Games" / "ACE" / "log.txt")
        if self._config.log_path == old_log_path:
            self._config.log_path = DEFAULT_LOG_PATH
            self.save()
        
        self._loaded = True
        return self._config

    def _backup_before_repair(self) -> Optional[Path]:
        """Copy an unusable config before replacing it with repaired values.

        The copy is intentionally made without reading or logging its
        contents.  If an earlier backup exists, retain it and choose the next
        numbered name so a later repair cannot destroy the only recovery copy.
        """
        backup_path = self.config_path.with_name(self.config_path.name + ".bak")
        suffix = 1
        while backup_path.exists():
            backup_path = self.config_path.with_name(
                f"{self.config_path.name}.bak.{suffix}"
            )
            suffix += 1
        try:
            shutil.copy2(self.config_path, backup_path)
            log_warning(Component.CONFIG, "Repaired invalid config; backup created")
            return backup_path
        except (OSError, shutil.Error) as e:
            # Repair can still proceed if a backup cannot be made.  Do not
            # include paths or config values in the diagnostic.
            log_warning(
                Component.CONFIG,
                f"Could not back up invalid config: {type(e).__name__}",
            )
            return None
    
    def save(self) -> bool:
        """
        Save current configuration to file.
        
        Returns:
            True if save was successful
        """
        if not self._config:
            return False
        
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config.to_dict(), f, indent=2)
            return True
        except IOError as e:
            log_warning(Component.CONFIG, f"Error saving config: {e}")
            return False

    def set(self, config: AppConfig) -> bool:
        """Replace the active configuration and persist it."""
        previous_config = self._config
        previous_loaded = self._loaded
        self._config = config
        self._loaded = True
        if self.save():
            return True

        self._config = previous_config
        self._loaded = previous_loaded
        return False
    
    def get(self) -> AppConfig:
        """
        Get current configuration.
        
        Returns:
            Current configuration (loads if not already loaded)
        """
        if not self._loaded:
            return self.load()
        return self._config or AppConfig()
    
    def update(self, **kwargs) -> AppConfig:
        """
        Update configuration values.
        
        Args:
            **kwargs: Configuration values to update
            
        Returns:
            Updated configuration
        """
        config = self.get()
        
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        self.save()
        return config
    
    def reset(self) -> AppConfig:
        """
        Reset configuration to defaults.
        
        Returns:
            Default configuration
        """
        self._config = AppConfig()
        self.save()
        return self._config
    
    def set_discord_config(
        self,
        webhook_url: Optional[str] = None,
        enabled: Optional[bool] = None,
        pb_only: Optional[bool] = None,
        post_invalid: Optional[bool] = None,
    ) -> None:
        """
        Set Discord configuration.
        
        Args:
            webhook_url: Discord webhook URL
            enabled: Whether Discord posting is enabled
            pb_only: Whether to only post personal bests
            post_invalid: Whether to post invalid laps
        """
        updates: dict[str, Any] = {}
        if webhook_url is not None:
            updates["discord_webhook_url"] = webhook_url
        if enabled is not None:
            updates["discord_enabled"] = enabled
        if pb_only is not None:
            updates["discord_pb_only"] = pb_only
        if post_invalid is not None:
            updates["submit_invalid_laps"] = post_invalid
        
        if updates:
            self.update(**updates)
