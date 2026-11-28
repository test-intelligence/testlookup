"""Cheap Linux worker launch/readiness checks, without importing Celery or metrics.

The launcher owns one /tmp state file and one multiprocess metric directory.
Its exec preserves the parent PID and forwards signals directly to Celery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys

READY_SENTINEL = "/tmp/celery-worker-ready"
GENERATION_ENV = "TESTLOOKUP_WORKER_PROCESS_GENERATION"
_MAX_STATE_BYTES = 2048


class WorkerStartupError(RuntimeError):
    """Finite startup failure with no credentials, paths or exception text."""


def process_identity(pid: int) -> dict | None:
    """Disambiguate PID reuse and container restarts without inspecting secrets."""
    if sys.platform != "linux" or pid < 1:
        return None
    try:
        root = Path("/proc") / str(pid)
        # comm can contain spaces and ')'; the last ')' ends that field.
        fields = (root / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X", "x"}:
            return None
        return {"pid": pid, "started": fields[19], "pid_namespace": (root / "ns/pid").stat().st_ino,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()}
    except (OSError, IndexError, ValueError):
        return None


def _owner_path(ready: Path) -> Path:
    return ready.with_name(ready.name + ".owner")


def _read(path: Path) -> dict | None:
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_STATE_BYTES:
            return None
        value = json.loads(path.read_bytes())
        if (not isinstance(value, dict) or set(value) != {"version", "generation", "identity"} or value["version"] != 1
                or not isinstance(value["generation"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["generation"])
                or not isinstance(value["identity"], dict) or set(value["identity"]) != {"pid", "started", "pid_namespace", "boot_id"}
                or type(value["identity"]["pid"]) is not int or value["identity"]["pid"] < 1):
            return None
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        return None


@contextmanager
def _state_lock(ready: Path):
    """Kernel-owned lock disappears on a crash, while the lock file may persist."""
    fd = os.open(ready.with_name(ready.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + os.urandom(8).hex())
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reset_metrics(directory: str | None) -> None:
    if not directory:
        return
    root = Path(directory)
    if not root.is_absolute() or root == Path(root.anchor) or root.is_symlink():
        raise WorkerStartupError("worker_metrics_directory_invalid")
    root.mkdir(parents=True, exist_ok=True)
    for entry in root.iterdir():
        if entry.name.endswith(".db"):
            if entry.is_symlink():
                raise WorkerStartupError("worker_metrics_directory_invalid")
            if entry.is_file():
                entry.unlink()


def prepared(ready_file: str = READY_SENTINEL) -> bool:
    owner = _read(_owner_path(Path(ready_file)))
    return bool(owner and owner["generation"] == os.environ.get(GENERATION_ENV)
                and owner["identity"] == process_identity(os.getpid()))


def prepare_startup(ready_file: str = READY_SENTINEL, *, force: bool = False) -> None:
    """Reset before framework imports, once per current parent generation."""
    ready = Path(ready_file)
    try:
        with _state_lock(ready):
            current = process_identity(os.getpid())
            if current is None:
                raise WorkerStartupError("worker_process_identity_unavailable")
            owner = _read(_owner_path(ready))
            if not force and prepared(ready_file):
                return
            if (owner and owner["identity"] != current
                    and owner["identity"] == process_identity(owner["identity"]["pid"])):
                raise WorkerStartupError("worker_state_has_live_owner")
            ready.unlink(missing_ok=True)
            _reset_metrics(os.environ.get("PROMETHEUS_MULTIPROC_DIR"))
            generation = os.urandom(16).hex()
            _write(_owner_path(ready), {"version": 1, "generation": generation, "identity": current})
            os.environ[GENERATION_ENV] = generation
    except OSError as error:
        raise WorkerStartupError("worker_startup_state_unavailable") from error


def mark_ready(ready_file: str = READY_SENTINEL) -> bool:
    """Only the prepared current worker parent can publish its own readiness."""
    ready = Path(ready_file)
    try:
        with _state_lock(ready):
            if not prepared(ready_file):
                return False
            owner = _read(_owner_path(ready))
            if owner is None:
                return False
            _write(ready, owner)
            return True
    except OSError:
        return False


def clear_current(ready_file: str = READY_SENTINEL) -> bool:
    """An old parent/child cannot clear a replacement parent's state."""
    ready = Path(ready_file)
    try:
        with _state_lock(ready):
            if not prepared(ready_file):
                return False
            owner = _read(_owner_path(ready))
            if _read(ready) == owner:
                ready.unlink(missing_ok=True)
            _owner_path(ready).unlink(missing_ok=True)
            os.environ.pop(GENERATION_ENV, None)
            return True
    except OSError:
        return False


def clear_ready(ready_file: str = READY_SENTINEL) -> bool:
    """Stop advertising consumption when warm shutdown begins, preserving liveness."""
    ready = Path(ready_file)
    try:
        with _state_lock(ready):
            if not prepared(ready_file):
                return False
            if _read(ready) == _read(_owner_path(ready)):
                ready.unlink(missing_ok=True)
            return True
    except OSError:
        return False


def _launcher_running(pid: int) -> bool:
    try:
        with (Path("/proc") / str(pid) / "cmdline").open("rb") as source:
            command = source.read(8192)
        return b"app.worker.worker_process" in command or b"worker_process.py" in command
    except OSError:
        return True


def probe(mode: str, *, pid: int = 1, ready_file: str = READY_SENTINEL) -> bool:
    """Validate only the container's current parent, with no broker round trip."""
    owner = _read(_owner_path(Path(ready_file)))
    if (not owner or owner["identity"]["pid"] != pid or owner["identity"] != process_identity(pid)
            or _launcher_running(pid)):
        return False
    return mode == "alive" or (mode == "ready" and _read(Path(ready_file)) == owner)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "probe":
        parser = argparse.ArgumentParser(description="Check current worker process ownership without importing Celery.")
        parser.add_argument("--mode", choices=("ready", "alive"), default="ready")
        parser.add_argument("--pid", type=int, default=1)
        parser.add_argument("--ready-file", default=READY_SENTINEL)
        options = parser.parse_args(arguments[1:])
        return 0 if options.pid > 0 and probe(options.mode, pid=options.pid, ready_file=options.ready_file) else 1
    if not arguments or arguments[0] != "celery" or "worker" not in arguments:
        sys.stderr.write("worker_launcher_requires_celery_worker\n")
        return 2
    try:
        prepare_startup(force=True)
        os.execvpe(arguments[0], arguments, os.environ)
    except (OSError, WorkerStartupError) as error:
        sys.stderr.write((str(error) if isinstance(error, WorkerStartupError) else "worker_exec_failed") + "\n")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
