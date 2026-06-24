"""Configuration management — loads API keys and settings from config file.

Priority: config.yaml > config.local.yaml > environment variables.
Default config path: project_root/config.yaml
Override with CONFIG_PATH environment variable.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_config: dict[str, Any] = {}
_loaded = False


def _find_config_path() -> Path | None:
    """Find the config file path."""
    # Environment variable override
    env_path = os.environ.get("CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # Search relative to project root
    project_root = Path(__file__).resolve().parent.parent
    candidates = [
        project_root / "config.local.yaml",  # Local overrides (gitignored)
        project_root / "config.yaml",         # Default config
        project_root / "config.local.yml",
        project_root / "config.yml",
    ]
    for p in candidates:
        if p.exists():
            return p

    return None


def load_config() -> dict[str, Any]:
    """Load configuration from YAML file."""
    global _config, _loaded

    if _loaded:
        return _config

    config_path = _find_config_path()
    if config_path is None:
        logger.warning("No config file found, using environment variables only")
        _config = {}
        _loaded = True
        return _config

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
        logger.info(f"Config loaded from: {config_path}")
    except ImportError:
        # Fallback: simple key=value parsing if PyYAML not available
        logger.warning("PyYAML not installed, trying simple parsing")
        _config = _parse_simple_config(config_path)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        _config = {}

    _loaded = True
    return _config


def _parse_simple_config(path: Path) -> dict[str, Any]:
    """Fallback parser for simple YAML (top-level keys only)."""
    result: dict[str, Any] = {}
    current_section = result
    current_key = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                current_section[key] = value
            else:
                result[key] = {}
                current_section = result[key]
                current_key = key

    return result


def get(key: str, default: str = "") -> str:
    """Get a config value by dot-notation key.

    Examples:
        get("digikey.client_id")
        get("mouser.api_key")
    """
    config = load_config()

    # Try dot-notation: "digikey.client_id" -> config["digikey"]["client_id"]
    parts = key.split(".")
    value = config
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = None
            break

    if value is not None and isinstance(value, str):
        return value

    # Fallback to environment variable (convert dot to underscore, uppercase)
    env_key = key.replace(".", "_").upper()
    return os.environ.get(env_key, default)


def get_section(section: str) -> dict[str, str]:
    """Get all keys in a config section."""
    config = load_config()
    section_data = config.get(section, {})
    if isinstance(section_data, dict):
        return {k: str(v) for k, v in section_data.items() if v}
    return {}


def reload() -> dict[str, Any]:
    """Force reload configuration."""
    global _loaded
    _loaded = False
    return load_config()
