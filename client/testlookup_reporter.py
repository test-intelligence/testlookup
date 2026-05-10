"""
TestLookup Reporter — Python Client SDK
========================================
Streams test execution events to a TestLookup server in real-time.

Designed for 10 000+ concurrent test executions:
  - Creates a lightweight session (one REST call at startup)
  - Batches events in memory and flushes every BATCH_INTERVAL_MS ms or
    every BATCH_SIZE events (whichever fires first)
  - Uses asyncio.Queue for thread-safe, non-blocking accumulation
  - Retries failed flushes with exponential back-off
  - Session completed automatically on context-manager exit

Quick start (async)
-------------------
    import asyncio
    from testlookup_reporter import TestLookupReporter

    async def main():
        reporter = TestLookupReporter(
            base_url="http://localhost:8000",
            token="<jwt>",
            project_id="<uuid>",
        )
        async with reporter.session(build_number="build-42") as s:
            await s.record("test_login",  "PASSED",  120)
            await s.record("test_logout", "FAILED",  340,
                           error="AssertionError: expected 200")

    asyncio.run(main())

Pytest plugin
-------------
Install:
    pip install testlookup-reporter

Run:
    pytest --testlookup-url http://localhost:8000 \\
           --testlookup-token <jwt> \\
           --testlookup-project <uuid> \\
           --testlookup-build   build-42

The plugin auto-collects results and streams them during the test run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx

__version__ = "1.0.0"
__all__ = ["TestLookupReporter", "LiveSession", "LiveStream"]

logger = logging.getLogger("testlookup_reporter")

# ── Defaults ──────────────────────────────────────────────────────────────────
BATCH_SIZE         = 50        # flush when queue reaches this size
BATCH_INTERVAL_MS  = 100       # flush at most every N ms even if queue is smaller
MAX_BATCH_SIZE     = 1_000     # hard cap per HTTP call
MAX_QUEUE_SIZE     = 50_000    # back-pressure: block producer if queue grows this large
MAX_RETRIES        = 5         # retries per flush on transient errors
RETRY_BASE_DELAY   = 0.5       # seconds
CONNECT_TIMEOUT    = 10.0
READ_TIMEOUT       = 30.0


# ── Configuration Loader ──────────────────────────────────────────────────────

class ConfigLoader:
    """
    Discovers and merges testlookup.yaml config with environment variables.

    Discovery order (first file found wins):
      1. ./testlookup.yaml
      2. ./.testlookup/config.yaml
      3. ~/.testlookup/config.yaml

    Precedence (highest wins):
      Constructor kwargs > Environment variables > Config file > Built-in defaults
    """

    from pathlib import Path as _Path

    SEARCH_PATHS = [
        _Path("testlookup.yaml"),
        _Path(".testlookup") / "config.yaml",
    ]

    ENV_MAP: dict[str, tuple[str, str]] = {
        "TESTLOOKUP_URL":         ("server", "url"),
        "TESTLOOKUP_TOKEN":       ("auth", "token"),
        "TESTLOOKUP_API_KEY":     ("auth", "api_key"),
        "TESTLOOKUP_PROJECT_ID":  ("project", "id"),
        "TESTLOOKUP_BUILD":       ("ci", "build_number"),
        "TESTLOOKUP_BRANCH":      ("ci", "branch"),
        "TESTLOOKUP_COMMIT":      ("ci", "commit_hash"),
        "TESTLOOKUP_UPLOAD_MODE": ("upload", "mode"),
    }

    @classmethod
    def load(cls, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Return merged config dict from file + env vars + overrides."""
        config: dict[str, Any] = {}

        # 1. Load from config file
        config_file = cls._find_config_file()
        if config_file:
            config = cls._parse_yaml(config_file)

        # 2. Overlay environment variables
        cls._apply_env_overlay(config)

        # 3. Overlay programmatic overrides (highest precedence)
        if overrides:
            cls._deep_merge(config, overrides)

        return config

    @classmethod
    def _find_config_file(cls):
        """Walk SEARCH_PATHS, return first existing file path or None."""
        from pathlib import Path

        # Project-level paths
        for p in cls.SEARCH_PATHS:
            if p.exists():
                return p

        # User home path
        home_cfg = Path.home() / ".testlookup" / "config.yaml"
        if home_cfg.exists():
            return home_cfg

        return None

    @classmethod
    def _parse_yaml(cls, path) -> dict[str, Any]:
        """Parse a YAML config file. Returns empty dict if pyyaml is not installed."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("pyyaml not installed — skipping config file %s", path)
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed to parse config file %s: %s", path, exc)
            return {}

    @classmethod
    def _apply_env_overlay(cls, config: dict[str, Any]) -> None:
        """Overlay environment variables onto config dict (mutates in place)."""
        for env_var, (section, key) in cls.ENV_MAP.items():
            val = os.environ.get(env_var)
            if val:
                config.setdefault(section, {})[key] = val

    @classmethod
    def _deep_merge(cls, base: dict, overlay: dict) -> None:
        """Recursively merge overlay into base (mutates base)."""
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                cls._deep_merge(base[k], v)
            elif v is not None:
                base[k] = v

    @classmethod
    def get(cls, config: dict[str, Any], dotpath: str, default: Any = None) -> Any:
        """Get a nested value using dot notation: 'server.url' → config['server']['url']."""
        parts = dotpath.split(".")
        cur = config
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return default
            if cur is None:
                return default
        return cur if cur != "" else default


# ── Reporter ──────────────────────────────────────────────────────────────────

class TestLookupReporter:
    """
    Entry point for creating live execution sessions.

    Parameters
    ----------
    base_url    : TestLookup server URL (e.g. "http://localhost:8000")
    token       : JWT access token obtained via /api/v1/auth/login
    project_id  : UUID of the target project
    client_name : Human-readable label for this machine (default: hostname)
    framework   : Test framework name (default: "python")
    batch_size  : Events per flush (default: 50)
    batch_interval_ms : Max ms between flushes (default: 100)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        project_id: Optional[str] = None,
        *,
        client_name: Optional[str] = None,
        framework: str = "python",
        batch_size: int = BATCH_SIZE,
        batch_interval_ms: int = BATCH_INTERVAL_MS,
        verify_ssl: bool = True,
    ) -> None:
        # Resolve config from file + env vars, then overlay constructor args
        cfg = ConfigLoader.load()
        resolved_url = base_url or ConfigLoader.get(cfg, "server.url")
        resolved_token = token or ConfigLoader.get(cfg, "auth.token") or ConfigLoader.get(cfg, "auth.api_key")
        resolved_project = project_id or ConfigLoader.get(cfg, "project.id")

        if not resolved_url or not resolved_token or not resolved_project:
            raise ValueError(
                "base_url, token/api_key, and project_id are required. "
                "Provide them as constructor args, in testlookup.yaml, "
                "or via TESTLOOKUP_URL / TESTLOOKUP_TOKEN / TESTLOOKUP_PROJECT_ID env vars."
            )

        self._base_url = resolved_url.rstrip("/")
        self._token = resolved_token
        self._project_id = resolved_project
        self._client_name = client_name or ConfigLoader.get(cfg, "reporting.client_name") or socket.gethostname()
        self._framework = framework
        self._batch_size = min(
            batch_size if batch_size != BATCH_SIZE else int(ConfigLoader.get(cfg, "reporting.batch_size", BATCH_SIZE)),
            MAX_BATCH_SIZE,
        )
        batch_ms = batch_interval_ms if batch_interval_ms != BATCH_INTERVAL_MS else int(ConfigLoader.get(cfg, "reporting.batch_interval_ms", BATCH_INTERVAL_MS))
        self._batch_interval = batch_ms / 1_000.0
        self._verify_ssl = verify_ssl

        # Determine auth header: API key vs JWT
        api_key = ConfigLoader.get(cfg, "auth.api_key")
        if api_key and (not token) and api_key == self._token:
            auth_headers = {"X-API-Key": self._token}
        else:
            auth_headers = {"Authorization": f"Bearer {self._token}"}

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=auth_headers,
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=10.0, pool=5.0),
            verify=self._verify_ssl,
        )

    @asynccontextmanager
    async def session(
        self,
        build_number: Optional[str] = None,
        branch: Optional[str] = None,
        commit_hash: Optional[str] = None,
        total_tests: Optional[int] = None,
        machine_id: Optional[str] = None,
    ):
        """
        Async context manager that manages the full session lifecycle:
          __aenter__ → register session with server
          yield      → LiveSession object for recording events
          __aexit__  → flush remaining events + mark session complete
        """
        live = await self._create_session(
            build_number=build_number,
            branch=branch,
            commit_hash=commit_hash,
            total_tests=total_tests,
            machine_id=machine_id,
        )
        try:
            yield live
        finally:
            await live._shutdown()
            await self._close_session(live.session_id)

    async def _create_session(self, **kwargs) -> "LiveSession":
        """Register a new session with the server and return a LiveSession."""
        payload: dict[str, Any] = {
            "project_id": self._project_id,
            "client_name": self._client_name,
            "framework": self._framework,
            "machine_id": kwargs.pop("machine_id") or socket.gethostname(),
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})

        resp = await self._http.post("/api/v1/stream/sessions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        logger.info(
            "Session registered: session_id=%s run_id=%s",
            data["session_id"], data["run_id"],
        )
        return LiveSession(
            session_id=data["session_id"],
            session_token=data["session_token"],
            run_id=data["run_id"],
            http=self._http,
            base_url=self._base_url,
            batch_size=self._batch_size,
            batch_interval=self._batch_interval,
        )

    async def _close_session(self, session_id: str) -> None:
        try:
            resp = await self._http.delete(f"/api/v1/stream/sessions/{session_id}")
            resp.raise_for_status()
            logger.info("Session closed: %s", session_id)
        except Exception as exc:
            logger.warning("Failed to close session %s: %s", session_id, exc)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ── Live Session ───────────────────────────────────────────────────────────────

class LiveSession:
    """
    Represents an active test execution session.

    Accumulates events in an asyncio.Queue and flushes them in batches to the
    server. Back-pressure is applied if the queue grows beyond MAX_QUEUE_SIZE.
    """

    def __init__(
        self,
        session_id: str,
        session_token: str,
        run_id: str,
        http: httpx.AsyncClient,
        base_url: str,
        batch_size: int,
        batch_interval: float,
    ) -> None:
        self.session_id = session_id
        self.session_token = session_token
        self.run_id = run_id
        self._http = http
        self._base_url = base_url
        self._batch_size = batch_size
        self._batch_interval = batch_interval

        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._flusher_task: Optional[asyncio.Task] = None
        self._stats = {"sent": 0, "failed": 0}

        # Start the background flusher
        self._flusher_task = asyncio.create_task(
            self._flusher_loop(), name=f"testlookup-flusher-{session_id[:8]}"
        )

    # ── Public API ────────────────────────────────────────────────────────

    async def record(
        self,
        test_name: str,
        status: str,
        duration_ms: int = 0,
        *,
        suite_name: Optional[str] = None,
        class_name: Optional[str] = None,
        error: Optional[str] = None,
        stack_trace: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Record a test result.

        Parameters
        ----------
        test_name   : Full test name / identifier
        status      : "PASSED" | "FAILED" | "SKIPPED" | "BROKEN"
        duration_ms : Execution duration in milliseconds
        suite_name  : Test suite / file name (optional)
        class_name  : Test class (optional)
        error       : Short error message (optional)
        stack_trace : Full stack trace (optional)
        tags        : List of tags (optional)
        metadata    : Extra key/value pairs (optional)
        """
        event: dict[str, Any] = {
            "event_type": "test_result",
            "test_name": test_name,
            "status": status.upper(),
            "duration_ms": duration_ms,
            "timestamp_ms": int(time.time() * 1_000),
        }
        if suite_name:    event["suite_name"]  = suite_name
        if class_name:    event["class_name"]  = class_name
        if error:         event["error_message"] = error
        if stack_trace:   event["stack_trace"] = stack_trace
        if tags:          event["tags"] = tags
        if metadata:      event["metadata"] = metadata

        await self._queue.put(event)

        # Flush eagerly if queue reached batch threshold
        if self._queue.qsize() >= self._batch_size:
            await self._flush_once()

    async def log(self, message: str, level: str = "INFO", metadata: Optional[dict] = None) -> None:
        """Record a log event (informational, not a test result)."""
        event: dict[str, Any] = {
            "event_type": "log",
            "test_name": None,
            "status": None,
            "metadata": {"level": level, "message": message, **(metadata or {})},
            "timestamp_ms": int(time.time() * 1_000),
        }
        await self._queue.put(event)

    async def metric(self, name: str, value: float, unit: str = "", metadata: Optional[dict] = None) -> None:
        """Record a numeric metric (e.g. memory usage, response time p99)."""
        event: dict[str, Any] = {
            "event_type": "metric",
            "test_name": name,
            "duration_ms": int(value),
            "metadata": {"value": value, "unit": unit, **(metadata or {})},
            "timestamp_ms": int(time.time() * 1_000),
        }
        await self._queue.put(event)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── Internal ──────────────────────────────────────────────────────────

    async def _flusher_loop(self) -> None:
        """Background task: periodically drains the queue regardless of size."""
        while True:
            try:
                await asyncio.sleep(self._batch_interval)
                await self._flush_once()
            except asyncio.CancelledError:
                # Re-raise immediately — do NOT attempt to flush here.
                # When a task is cancelled, asyncio sets _must_cancel=True, which
                # causes any subsequent await inside this handler to be cancelled
                # too (the underlying future is cancelled before the coroutine even
                # sends the HTTP request).  _shutdown() handles the final drain in
                # the non-cancelled caller coroutine where awaits work normally.
                raise
            except Exception as exc:
                logger.debug("Flusher loop error (non-fatal): %s", exc)

    async def _flush_once(self) -> None:
        """Drain up to BATCH_SIZE events from the queue and POST them."""
        if self._queue.empty():
            return

        batch: list[dict] = []
        try:
            for _ in range(self._batch_size):
                batch.append(self._queue.get_nowait())
        except asyncio.QueueEmpty:
            pass

        if not batch:
            return

        await self._post_batch(batch)

    async def _flush_all(self) -> None:
        """Drain all remaining events (called on shutdown)."""
        while not self._queue.empty():
            batch: list[dict] = []
            try:
                for _ in range(MAX_BATCH_SIZE):
                    batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                pass
            if batch:
                await self._post_batch(batch)

    async def _post_batch(self, events: list[dict]) -> None:
        """POST a batch to the server with exponential back-off on failure."""
        payload = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "events": events,
        }
        headers = {"X-Session-Token": self.session_token}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._http.post(
                    "/api/v1/stream/events/batch",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 401:
                    logger.error("Session token rejected — stopping flush")
                    self._stats["failed"] += len(events)
                    return

                resp.raise_for_status()
                data = resp.json()
                self._stats["sent"] += data.get("accepted", len(events))
                return

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                if attempt == MAX_RETRIES:
                    logger.error(
                        "Batch POST failed after %d attempts (%d events lost): %s",
                        attempt, len(events), exc,
                    )
                    self._stats["failed"] += len(events)
                    return
                logger.warning(
                    "Batch POST attempt %d failed, retrying in %.1fs: %s",
                    attempt, delay, exc,
                )
                await asyncio.sleep(delay)

            except Exception as exc:
                logger.error("Unexpected batch POST error (%d events): %s", len(events), exc)
                self._stats["failed"] += len(events)
                return

    async def _shutdown(self) -> None:
        """Cancel the flusher task and drain any remaining events.

        The final flush MUST happen here (in the non-cancelled caller coroutine),
        not inside the flusher task's CancelledError handler.  When a task is
        cancelled, asyncio's internal _must_cancel flag causes every subsequent
        await inside that task to be cancelled as well — so any HTTP call made
        from the CancelledError handler is dropped silently.  By flushing here
        we avoid that pitfall entirely.
        """
        if self._flusher_task and not self._flusher_task.done():
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass

        # Drain anything the flusher didn't send (runs in a non-cancelled context)
        await self._flush_all()

        logger.info(
            "Session %s shutdown: sent=%d failed=%d",
            self.session_id[:8], self._stats["sent"], self._stats["failed"],
        )


# ── API-key-only Live Stream ──────────────────────────────────────────────────

class LiveStream:
    """Stream test results using only an API key — no /sessions ceremony.

    The legacy ``TestLookupReporter`` requires the caller to supply a
    ``project_id`` plus a bag of CI metadata, then orchestrates an
    ``open session → batch → close`` round-trip. ``LiveStream`` skips all of
    that — the server derives ``project_id`` from the project-scoped API key
    and auto-creates the live session on the first batch.

    Quick start
    -----------
        async with LiveStream(api_key="tlk_...", run_id="ci-build-42") as s:
            await s.record("test_login", "PASSED", 120)
            await s.record("test_logout", "FAILED", 340, error="...")

    The API key must be **project-scoped** and carry the ``stream:write``
    scope. Mint one in Settings → API Keys.

    Parameters
    ----------
    api_key     : Project-scoped API key (from env TESTLOOKUP_API_KEY by default)
    run_id      : Stable identifier for this run (CI build id, UUID, …)
    base_url    : Server URL (env TESTLOOKUP_URL by default)
    build_number, branch, commit_hash, framework, total_tests, machine_id,
    release_name, metadata : Optional CI metadata sent with the first batch.
    batch_size, batch_interval_ms, verify_ssl : Batching/transport tuning.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        run_id: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        build_number: Optional[str] = None,
        branch: Optional[str] = None,
        commit_hash: Optional[str] = None,
        framework: Optional[str] = "python",
        total_tests: Optional[int] = None,
        machine_id: Optional[str] = None,
        release_name: Optional[str] = None,
        metadata: Optional[dict] = None,
        batch_size: int = BATCH_SIZE,
        batch_interval_ms: int = BATCH_INTERVAL_MS,
        verify_ssl: bool = True,
    ) -> None:
        cfg = ConfigLoader.load()
        resolved_url = base_url or ConfigLoader.get(cfg, "server.url")
        resolved_key = api_key or ConfigLoader.get(cfg, "auth.api_key") or os.environ.get("TESTLOOKUP_API_KEY")
        if not resolved_url:
            raise ValueError(
                "base_url is required (constructor arg, server.url in testlookup.yaml, or TESTLOOKUP_URL env var)"
            )
        if not resolved_key:
            raise ValueError(
                "api_key is required (constructor arg, auth.api_key in testlookup.yaml, or TESTLOOKUP_API_KEY env var)"
            )
        if not run_id:
            raise ValueError("run_id is required — pick any stable identifier for this run")

        self._base_url = resolved_url.rstrip("/")
        self._api_key = resolved_key
        self._run_id = run_id
        self._batch_size = min(batch_size, MAX_BATCH_SIZE)
        self._batch_interval = batch_interval_ms / 1_000.0

        # Meta is sent on every call but only used by the server on the first
        # call to populate the auto-created session. Sending it on subsequent
        # calls is a no-op server-side.
        self._meta: dict[str, Any] = {}
        if build_number is not None: self._meta["build_number"] = build_number
        if branch is not None:       self._meta["branch"] = branch
        if commit_hash is not None:  self._meta["commit_hash"] = commit_hash
        if framework is not None:    self._meta["framework"] = framework
        if total_tests is not None:  self._meta["total_tests"] = total_tests
        if machine_id is not None:   self._meta["machine_id"] = machine_id
        if release_name is not None: self._meta["release_name"] = release_name
        if metadata is not None:     self._meta["metadata"] = metadata

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": self._api_key},
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=10.0, pool=5.0),
            verify=verify_ssl,
        )

        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._flusher_task: Optional[asyncio.Task] = None
        self._stats = {"sent": 0, "failed": 0}
        self._session_id: Optional[str] = None  # set by the server on first batch

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def __aenter__(self) -> "LiveStream":
        self._flusher_task = asyncio.create_task(
            self._flusher_loop(), name=f"testlookup-livestream-{self._run_id[:24]}"
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Drain pending events, finalize the run, and close the HTTP client.

        On exit we post a ``run_complete`` event so the server closes the live
        session, runs ``upsert_test_run``, and queues the ingestion + analysis
        pipelines. Without this the session stays ``active`` in the DB and the
        run never shows up in Runs / Overview / Coverage / Failures / Trends.
        """
        if self._flusher_task and not self._flusher_task.done():
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
        await self._flush_all()

        # Finalize the run server-side. Best-effort: a network error here
        # shouldn't mask an exception from the test body that prompted close().
        try:
            await self._post_batch([{
                "event_type": "run_complete",
                "timestamp_ms": int(time.time() * 1_000),
            }])
        except Exception as exc:  # pragma: no cover - logged, non-fatal
            logger.warning("LiveStream %s run_complete post failed: %s", self._run_id, exc)

        await self._http.aclose()
        logger.info(
            "LiveStream %s closed: sent=%d failed=%d",
            self._run_id, self._stats["sent"], self._stats["failed"],
        )

    # ── Public API (mirrors LiveSession) ──────────────────────────────────

    async def record(
        self,
        test_name: str,
        status: str,
        duration_ms: int = 0,
        *,
        suite_name: Optional[str] = None,
        class_name: Optional[str] = None,
        error: Optional[str] = None,
        stack_trace: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        event: dict[str, Any] = {
            "event_type": "test_result",
            "test_name": test_name,
            "status": status.upper(),
            "duration_ms": duration_ms,
            "timestamp_ms": int(time.time() * 1_000),
        }
        if suite_name:    event["suite_name"]  = suite_name
        if class_name:    event["class_name"]  = class_name
        if error:         event["error_message"] = error
        if stack_trace:   event["stack_trace"] = stack_trace
        if tags:          event["tags"] = tags
        if metadata:      event["metadata"] = metadata

        await self._queue.put(event)
        if self._queue.qsize() >= self._batch_size:
            await self._flush_once()

    async def log(self, message: str, level: str = "INFO", metadata: Optional[dict] = None) -> None:
        await self._queue.put({
            "event_type": "log",
            "test_name": None,
            "status": None,
            "metadata": {"level": level, "message": message, **(metadata or {})},
            "timestamp_ms": int(time.time() * 1_000),
        })

    async def metric(self, name: str, value: float, unit: str = "", metadata: Optional[dict] = None) -> None:
        await self._queue.put({
            "event_type": "metric",
            "test_name": name,
            "duration_ms": int(value),
            "metadata": {"value": value, "unit": unit, **(metadata or {})},
            "timestamp_ms": int(time.time() * 1_000),
        })

    @property
    def stats(self) -> dict:
        return {**self._stats, "session_id": self._session_id, "run_id": self._run_id}

    # ── Internal ──────────────────────────────────────────────────────────

    async def _flusher_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._batch_interval)
                await self._flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("LiveStream flusher loop error (non-fatal): %s", exc)

    async def _flush_once(self) -> None:
        if self._queue.empty():
            return
        batch: list[dict] = []
        try:
            for _ in range(self._batch_size):
                batch.append(self._queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        if batch:
            await self._post_batch(batch)

    async def _flush_all(self) -> None:
        while not self._queue.empty():
            batch: list[dict] = []
            try:
                for _ in range(MAX_BATCH_SIZE):
                    batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                pass
            if batch:
                await self._post_batch(batch)

    async def _post_batch(self, events: list[dict]) -> None:
        payload: dict[str, Any] = {"run_id": self._run_id, "events": events}
        if self._meta:
            payload["meta"] = self._meta

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await self._http.post("/api/v1/stream/ingest", json=payload)
                if resp.status_code in (401, 403):
                    logger.error(
                        "LiveStream auth failed (%s) — stopping flush: %s",
                        resp.status_code, resp.text,
                    )
                    self._stats["failed"] += len(events)
                    return
                resp.raise_for_status()
                data = resp.json()
                self._stats["sent"] += data.get("accepted", len(events))
                if not self._session_id:
                    self._session_id = data.get("session_id")
                return
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                if attempt == MAX_RETRIES:
                    logger.error(
                        "LiveStream POST failed after %d attempts (%d events lost): %s",
                        attempt, len(events), exc,
                    )
                    self._stats["failed"] += len(events)
                    return
                logger.warning(
                    "LiveStream POST attempt %d failed, retrying in %.1fs: %s",
                    attempt, delay, exc,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                logger.error("Unexpected LiveStream POST error (%d events): %s", len(events), exc)
                self._stats["failed"] += len(events)
                return


# ── Pytest Plugin ─────────────────────────────────────────────────────────────
#
# Register automatically when installed:
#   [options.entry_points]
#   pytest11 = testlookup = testlookup_reporter:pytest_plugin
#
# Or load manually in conftest.py:
#   pytest_plugins = ["testlookup_reporter"]

def pytest_addoption(parser):  # noqa: D401
    """Add TestLookup CLI options to pytest."""
    group = parser.getgroup("testlookup", "TestLookup live reporting")
    group.addoption("--testlookup-url",     default=os.environ.get("TESTLOOKUP_URL", ""))
    group.addoption("--testlookup-token",   default=os.environ.get("TESTLOOKUP_TOKEN", ""))
    group.addoption("--testlookup-project", default=os.environ.get("TESTLOOKUP_PROJECT_ID", ""))
    group.addoption("--testlookup-build",   default=os.environ.get("TESTLOOKUP_BUILD", ""))
    group.addoption("--testlookup-branch",  default=os.environ.get("TESTLOOKUP_BRANCH", ""))


def pytest_configure(config):  # noqa: D401
    """Attach the reporter plugin if configuration is present.

    Resolution order: CLI flags → env vars → testlookup.yaml config file.
    If a testlookup.yaml exists, the plugin activates without any CLI flags.
    """
    url     = config.getoption("--testlookup-url",     default="")
    token   = config.getoption("--testlookup-token",   default="")
    project = config.getoption("--testlookup-project", default="")
    build   = config.getoption("--testlookup-build",   default="")
    branch  = config.getoption("--testlookup-branch",  default="")

    # If CLI flags are incomplete, try ConfigLoader (file + env vars)
    if not (url and token and project):
        cfg = ConfigLoader.load()
        url     = url     or ConfigLoader.get(cfg, "server.url", "")
        token   = token   or ConfigLoader.get(cfg, "auth.token", "") or ConfigLoader.get(cfg, "auth.api_key", "")
        project = project or ConfigLoader.get(cfg, "project.id", "")
        build   = build   or ConfigLoader.get(cfg, "ci.build_number", "")
        branch  = branch  or ConfigLoader.get(cfg, "ci.branch", "")

    if url and token and project:
        plugin = _TestLookupPytestPlugin(
            base_url=url,
            token=token,
            project_id=project,
            build_number=build,
            branch=branch,
        )
        config.pluginmanager.register(plugin, "testlookup_live")


class _TestLookupPytestPlugin:
    """Pytest plugin that streams results to TestLookup during the test run."""

    def __init__(
        self,
        base_url: str,
        token: str,
        project_id: str,
        build_number: str = "",
        branch: str = "",
    ) -> None:
        self._reporter = TestLookupReporter(
            base_url=base_url,
            token=token,
            project_id=project_id,
            framework="pytest",
        )
        self._build_number = build_number or f"pytest-{int(time.time())}"
        self._branch = branch
        self._live: Optional[LiveSession] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── pytest hooks ──────────────────────────────────────────────────────

    def pytest_sessionstart(self, session):
        self._loop = asyncio.new_event_loop()
        self._live = self._loop.run_until_complete(
            self._reporter._create_session(
                build_number=self._build_number,
                branch=self._branch or None,
            )
        )
        logger.info("TestLookup live session started: %s", self._live.session_id)

    def pytest_runtest_logreport(self, report):
        if report.when != "call" or self._live is None or self._loop is None:
            return

        status = "PASSED"
        error: Optional[str] = None
        stack: Optional[str] = None

        if report.failed:
            status = "FAILED"
            if report.longrepr:
                full = str(report.longrepr)
                # First line as short error; full repr as stack trace
                lines = full.splitlines()
                error = lines[-1] if lines else "Test failed"
                stack = full
        elif report.skipped:
            status = "SKIPPED"

        duration_ms = int(getattr(report, "duration", 0) * 1_000)

        # Derive suite name from node id (file path part)
        parts = report.nodeid.split("::")
        suite = parts[0] if len(parts) > 1 else ""
        test_name = "::".join(parts[1:]) if len(parts) > 1 else report.nodeid

        self._loop.run_until_complete(
            self._live.record(
                test_name=test_name,
                status=status,
                duration_ms=duration_ms,
                suite_name=suite,
                error=error,
                stack_trace=stack,
            )
        )

    def pytest_sessionfinish(self, session, exitstatus):
        if self._live is None or self._loop is None:
            return
        self._loop.run_until_complete(self._live._shutdown())
        self._loop.run_until_complete(
            self._reporter._close_session(self._live.session_id)
        )
        self._loop.run_until_complete(self._reporter.aclose())
        self._loop.close()
        logger.info(
            "TestLookup session finished: sent=%d failed=%d",
            self._live.stats["sent"], self._live.stats["failed"],
        )


# ── Synchronous convenience wrapper ──────────────────────────────────────────

class SyncLiveSession:
    """
    Thread-safe synchronous wrapper around LiveSession.

    Use this in synchronous test frameworks (unittest, pytest without asyncio).

        reporter = TestLookupReporter(...)
        with reporter.sync_session("build-42") as s:
            s.record_sync("test_login", "PASSED", 120)
    """

    def __init__(self, async_session: LiveSession, loop: asyncio.AbstractEventLoop) -> None:
        self._session = async_session
        self._loop = loop

    def record_sync(self, test_name: str, status: str, duration_ms: int = 0, **kwargs) -> None:
        self._loop.run_until_complete(
            self._session.record(test_name, status, duration_ms, **kwargs)
        )

    def log_sync(self, message: str, level: str = "INFO") -> None:
        self._loop.run_until_complete(self._session.log(message, level))

    @property
    def stats(self) -> dict:
        return self._session.stats
