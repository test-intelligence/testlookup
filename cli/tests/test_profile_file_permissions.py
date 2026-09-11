"""CLI profiles hold API keys and tokens: owner-only and atomic (re-audit L5).

``profiles.json`` was written with ``Path.write_text``: mode 0644 under the
usual umask (readable by every local user) and truncate-then-write (a crash
mid-write left an empty or half-written file of credentials).
"""
import json
import os
import stat

import pytest
from testlookup_cli import config

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    home = tmp_path / "testlookup"
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "PROFILES_FILE", home / "profiles.json")
    monkeypatch.setattr(config, "ACTIVE_FILE", home / "active_profile")
    monkeypatch.delenv("TESTLOOKUP_PROFILE", raising=False)
    return home


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_a_saved_profile_is_owner_only_even_over_a_world_readable_file(cfg):
    old_umask = os.umask(0o022)
    try:
        cfg.mkdir(mode=0o755)
        os.chmod(cfg, 0o755)
        config.PROFILES_FILE.write_text("{}")
        os.chmod(config.PROFILES_FILE, 0o644)

        config.save_profile("prod", {"url": "https://x", "api_key": "tl_secret"})
    finally:
        os.umask(old_umask)

    assert _mode(config.PROFILES_FILE) == 0o600
    assert _mode(cfg) == 0o700
    assert json.loads(config.PROFILES_FILE.read_text())["prod"]["api_key"] == "tl_secret"


@posix_only
def test_the_active_profile_file_is_owner_only(cfg):
    config.set_active_profile("prod")
    assert _mode(config.ACTIVE_FILE) == 0o600
    assert config.get_active_profile_name() == "prod"


def test_the_posix_branch_restricts_the_temp_file_before_any_secret_is_written(cfg, monkeypatch):
    """Runs on every OS: records the chmod calls the POSIX branch makes."""
    calls = []

    def record(path, mode):
        size = os.path.getsize(path) if os.path.isfile(path) else None
        calls.append((str(path), mode, size))

    monkeypatch.setattr(config, "_IS_POSIX", True)
    monkeypatch.setattr(config.os, "chmod", record)
    config.save_profile("prod", {"api_key": "tl_secret"})

    assert (str(cfg), 0o700, None) in calls
    file_calls = [c for c in calls if c[1] == 0o600]
    assert len(file_calls) == 1
    temp_path, _, size_at_chmod = file_calls[0]
    assert size_at_chmod == 0, "mode must be set before the secret is written"
    assert os.path.dirname(temp_path) == str(cfg)
    assert temp_path != str(config.PROFILES_FILE), "chmod the temp file, then rename"


def test_windows_does_not_chmod_and_still_saves(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "_IS_POSIX", False)
    monkeypatch.setattr(config.os, "chmod", lambda *a: calls.append(a))
    config.save_profile("dev", {"url": "http://localhost:8000"})
    assert calls == []
    assert config.get_profile("dev")["url"] == "http://localhost:8000"


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(cfg, monkeypatch):
    config.save_profile("prod", {"api_key": "old"})
    before = config.PROFILES_FILE.read_bytes()

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        config.save_profile("prod", {"api_key": "new"})

    assert config.PROFILES_FILE.read_bytes() == before
    assert sorted(p.name for p in cfg.iterdir()) == ["profiles.json"]


def test_profiles_round_trip(cfg):
    config.save_profile("a", {"url": "http://a"})
    config.save_profile("b", {"url": "http://b"})
    config.set_active_profile("b")
    assert config.delete_profile("a") is True
    assert [p["name"] for p in config.list_profiles()] == ["b"]
    assert config.list_profiles()[0]["active"] is True


def test_the_active_profile_is_written_through_the_private_writer(cfg, monkeypatch):
    """Cross-platform twin of the POSIX mode test for ``active_profile``."""
    modes = []
    monkeypatch.setattr(config, "_IS_POSIX", True)
    monkeypatch.setattr(config.os, "chmod", lambda path, mode: modes.append(mode))
    config.set_active_profile("prod")
    assert modes.count(0o600) == 1
    assert config.ACTIVE_FILE.read_text() == "prod"
