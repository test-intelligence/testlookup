"""CLI configuration — profiles, config dir, and environment resolution."""
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir

APP_NAME = "testlookup"
CONFIG_DIR = Path(user_config_dir(APP_NAME))
PROFILES_FILE = CONFIG_DIR / "profiles.json"
ACTIVE_FILE = CONFIG_DIR / "active_profile"

# profiles.json holds API keys and refresh tokens (re-audit L5). On POSIX it is
# written owner-only; Windows has no POSIX mode bits (os.chmod only toggles the
# read-only flag), and the per-user %APPDATA% ACL is what protects it there.
_IS_POSIX = os.name == "posix"
_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
    if _IS_POSIX:
        # mkdir's mode is filtered by the umask and ignored for a directory
        # that already exists, so set it explicitly.
        os.chmod(CONFIG_DIR, _DIR_MODE)


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, owner-only on POSIX.

    The temp file comes from ``mkstemp`` (mode 0600 from creation, so the
    secret is never readable by others, not even briefly), is fsynced, then
    renamed over the target: a crash leaves the old file or the new one, never
    a truncated one, and a pre-existing 0644 file is REPLACED by a 0600 one
    rather than rewritten in place with its old mode.
    """
    _ensure_config_dir()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if _IS_POSIX:
                os.chmod(tmp, _FILE_MODE)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _load_profiles() -> dict:
    if PROFILES_FILE.exists():
        return json.loads(PROFILES_FILE.read_text())
    return {}


def _save_profiles(profiles: dict) -> None:
    _write_private(PROFILES_FILE, json.dumps(profiles, indent=2))


def get_active_profile_name() -> str:
    """Get the active profile name (env override > file > 'default')."""
    env = os.environ.get("TESTLOOKUP_PROFILE")
    if env:
        return env
    if ACTIVE_FILE.exists():
        return ACTIVE_FILE.read_text().strip()
    return "default"


def set_active_profile(name: str) -> None:
    _write_private(ACTIVE_FILE, name)


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
