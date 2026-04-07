"""CLI configuration — profiles, config dir, and environment resolution."""
import json
import os
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir

APP_NAME = "testlookup"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
PROFILES_FILE = CONFIG_DIR / "profiles.json"
ACTIVE_FILE = CONFIG_DIR / "active_profile"


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_profiles() -> dict:
    if PROFILES_FILE.exists():
        return json.loads(PROFILES_FILE.read_text())
    return {}


def _save_profiles(profiles: dict) -> None:
    _ensure_config_dir()
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2))


def get_active_profile_name() -> str:
    """Get the active profile name (env override > file > 'default')."""
    env = os.environ.get("TESTLOOKUP_PROFILE")
    if env:
        return env
    if ACTIVE_FILE.exists():
        return ACTIVE_FILE.read_text().strip()
    return "default"


def set_active_profile(name: str) -> None:
    _ensure_config_dir()
    ACTIVE_FILE.write_text(name)


def get_profile(name: Optional[str] = None) -> dict:
    """Load a profile by name. Falls back to env vars if no profile found."""
    name = name or get_active_profile_name()
    profiles = _load_profiles()

    if name in profiles:
        return profiles[name]

    # Fallback to env vars
    url = os.environ.get("TESTLOOKUP_URL", "http://localhost:8000")
    api_key = os.environ.get("TESTLOOKUP_API_KEY")
    return {
        "name": name,
        "url": url,
        "auth_type": "api_key" if api_key else "jwt",
        "api_key": api_key,
    }


def save_profile(name: str, profile: dict) -> None:
    """Save a profile to disk."""
    profiles = _load_profiles()
    profile["name"] = name
    profiles[name] = profile
    _save_profiles(profiles)


def delete_profile(name: str) -> bool:
    """Delete a profile. Returns True if it existed."""
    profiles = _load_profiles()
    if name in profiles:
        del profiles[name]
        _save_profiles(profiles)
        return True
    return False


def list_profiles() -> list[dict]:
    """List all saved profiles."""
    profiles = _load_profiles()
    active = get_active_profile_name()
    result = []
    for name, p in profiles.items():
        result.append({**p, "name": name, "active": name == active})
    return result
