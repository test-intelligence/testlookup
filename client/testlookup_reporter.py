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
# Hard cap on the wall-clock time a single batch can spend retrying.
# When a misconfigured project or a sustained server-side outage keeps
# returning 429/503, the SDK eventually surfaces a hard failure instead
# of buffering forever. 5 min matches the server-side fixed-bucket
# refresh window so a single bucket-empty stretch can still be ridden
# out, but two back-to-back outages cannot silently hide.
MAX_RETRY_TOTAL_SECONDS = 300.0
# HTTP status codes that count as transient server-side back-pressure.
# 429 → admission-gate bucket exhausted; 503 → Redis/backpressure rejection.
RETRYABLE_STATUS_CODES = frozenset({429, 503})
CONNECT_TIMEOUT    = 10.0
READ_TIMEOUT       = 30.0
# Emit a ``live_heartbeat`` event whenever the SDK has been silent this long.
# The server uses Redis ``last_event_at`` to drive its 5-minute idle-session
# reaper; without the heartbeat a legitimate run with a single long test
# would be falsely closed mid-flight. 30s is well under the reaper threshold
# so genuinely-dead clients still get reaped promptly.
HEARTBEAT_INTERVAL = 30.0      # seconds


# ── Retry policy helpers ──────────────────────────────────────────────────────
#
# The two ``_post_batch`` paths (``LiveSession`` and ``LiveStream``) share an
# identical retry contract: transport errors AND HTTP 429 / 503 retry,
# honouring ``Retry-After`` exactly when the server sends one, falling back
# to exponential backoff otherwise, capped at ``MAX_RETRY_TOTAL_SECONDS``.
# Centralising the decision here lets the test suite pin the rules without
# fakes around httpx.

def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse an HTTP ``Retry-After`` header value into seconds.

    RFC 9110 §10.2.3 allows either a delta-seconds integer (e.g. ``"30"``)
    or an HTTP-date. We accept the integer form natively and try
    ``email.utils.parsedate_to_datetime`` for the date form so a server
    answering ``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`` is still
    honoured. Returns ``None`` when the header is missing or unparseable
    so the caller can fall back to its backoff schedule.
    """
    if not header_value:
        return None
    value = header_value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(value)
        if target is None:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc) if target.tzinfo else datetime.now()
        delta = (target - now).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def _compute_next_retry_delay(
    *,
    retry_after_header: Optional[str],
    attempt: int,
    total_elapsed_s: float,
    base_delay_s: float = RETRY_BASE_DELAY,
    cap_total_s: float = MAX_RETRY_TOTAL_SECONDS,
) -> Optional[float]:
    """Return the seconds to wait before the next retry, or ``None``
    when the cumulative retry budget would be blown.

    Precedence: an explicit ``Retry-After`` header wins absolutely (we
    honour it exactly, no doubling/halving). Without one we fall back
    to the existing exponential schedule.
    """
    if total_elapsed_s >= cap_total_s:
        return None
    parsed = _parse_retry_after(retry_after_header)
    delay = parsed if parsed is not None else base_delay_s * (2 ** max(0, attempt - 1))
    remaining = cap_total_s - total_elapsed_s
    if delay > remaining:
        # The header (or backoff) would push us past the wall — surface
        # the hard failure instead of stretching forever.
        return None
    return delay


# ── Configuration Loader ──────────────────────────────────────────────────────

class ConfigLoader:
    """
    Discovers and merges declarative TestLookup config from a properties file
    or YAML file, plus environment variables.

    Discovery order (first file found wins):
      1. ./testlookup.properties      (preferred, ReportPortal-compatible)
      2. ./testlookup.yaml
      3. ./.testlookup/testlookup.properties
      4. ./.testlookup/config.yaml
      5. ~/.testlookup/testlookup.properties
      6. ~/.testlookup/config.yaml

    Precedence (highest wins):
      Constructor kwargs > Environment variables > Config file > Built-in defaults

    Canonical key prefix is ``testlookup.*`` (analogous to ReportPortal's
    ``rp.*``). The legacy nested YAML schema (``server.url``, ``auth.api_key``,
    ``ci.build_number``) keeps working because the loader maps the canonical
    keys onto it.
    """

    from pathlib import Path as _Path

    PROPERTIES_PATHS = [
        _Path("testlookup.properties"),
        _Path(".testlookup") / "testlookup.properties",
    ]
    YAML_PATHS = [
        _Path("testlookup.yaml"),
        _Path(".testlookup") / "config.yaml",
    ]
    SEARCH_PATHS = YAML_PATHS  # legacy alias used by tests

    # Canonical testlookup.* keys → nested YAML/dict path. Both the
    # properties-file parser and the env-var overlay route through this
    # mapping so all three surfaces stay aligned.
    CANONICAL_MAP: dict[str, tuple[str, str]] = {
        "testlookup.endpoint":     ("server", "url"),
        "testlookup.url":          ("server", "url"),         # legacy alias
        "testlookup.token":        ("auth", "token"),
        "testlookup.api.key":      ("auth", "api_key"),
        "testlookup.api_key":      ("auth", "api_key"),       # underscore form
        "testlookup.project":      ("project", "id"),
        "testlookup.launch":       ("reporting", "launch_name"),
        "testlookup.suite":        ("reporting", "suite_name"),
        "testlookup.release":      ("reporting", "release_name"),
        "testlookup.build":        ("ci", "build_number"),
        "testlookup.branch":       ("ci", "branch"),
        "testlookup.commit":       ("ci", "commit_hash"),
        "testlookup.framework":    ("reporting", "framework"),
        # TLS — homelab / dev convenience. Either point at a custom CA bundle
        # (preferred — preserves verification) or disable verification entirely.
        "testlookup.ca_cert_path": ("auth", "ca_cert_path"),
        "testlookup.insecure":     ("auth", "insecure"),
    }

    ENV_MAP: dict[str, tuple[str, str]] = {
        # Legacy names — kept working forever for backwards compatibility.
        "TESTLOOKUP_URL":         ("server", "url"),
        "TESTLOOKUP_TOKEN":       ("auth", "token"),
        "TESTLOOKUP_API_KEY":     ("auth", "api_key"),
        "TESTLOOKUP_PROJECT_ID":  ("project", "id"),
        "TESTLOOKUP_BUILD":       ("ci", "build_number"),
        "TESTLOOKUP_BRANCH":      ("ci", "branch"),
        "TESTLOOKUP_COMMIT":      ("ci", "commit_hash"),
        "TESTLOOKUP_UPLOAD_MODE": ("upload", "mode"),
        # Canonical names matching the ReportPortal rp.* convention.
        "TESTLOOKUP_ENDPOINT":    ("server", "url"),
        "TESTLOOKUP_PROJECT":     ("project", "id"),
        "TESTLOOKUP_CA_CERT":     ("auth", "ca_cert_path"),
        "TESTLOOKUP_INSECURE":    ("auth", "insecure"),
        "TESTLOOKUP_LAUNCH":      ("reporting", "launch_name"),
        "TESTLOOKUP_SUITE":       ("reporting", "suite_name"),
        "TESTLOOKUP_RELEASE":     ("reporting", "release_name"),
        "TESTLOOKUP_FRAMEWORK":   ("reporting", "framework"),
    }

    @classmethod
    def load(cls, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Return merged config dict from file + env vars + overrides."""
        config: dict[str, Any] = {}

        config_file = cls._find_config_file()
        if config_file:
            if str(config_file).endswith(".properties"):
                config = cls._parse_properties(config_file)
            else:
                config = cls._parse_yaml(config_file)

        cls._apply_env_overlay(config)

        if overrides:
            cls._deep_merge(config, overrides)

        return config

    @classmethod
    def _find_config_file(cls):
        """Walk all known locations, return first existing file path or None."""
        from pathlib import Path

        # Project-level paths — properties first, then YAML
        candidates = list(cls.PROPERTIES_PATHS) + list(cls.YAML_PATHS)
        for p in candidates:
            if p.exists():
                return p

        # User home paths — properties first, then YAML
        home = Path.home() / ".testlookup"
        for name in ("testlookup.properties", "config.yaml"):
            home_cfg = home / name
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
    def _parse_properties(cls, path) -> dict[str, Any]:
        """Parse a Java-style .properties file with testlookup.* canonical keys.

        Lines starting with ``#`` or ``!`` are comments; blank lines are
        skipped. Each remaining line is split on the first ``=`` or ``:``.
        Unknown keys are ignored with a debug log; recognised keys land in
        the canonical nested config so the rest of the loader behaves as if
        the values had come from YAML.
        """
        config: dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or line.startswith("!"):
                        continue
                    sep_idx = -1
                    for sep in ("=", ":"):
                        idx = line.find(sep)
                        if idx >= 0 and (sep_idx < 0 or idx < sep_idx):
                            sep_idx = idx
                    if sep_idx < 0:
                        continue
                    key = line[:sep_idx].strip()
                    val = line[sep_idx + 1:].strip()
                    target = cls.CANONICAL_MAP.get(key)
                    if not target:
                        logger.debug("Unknown testlookup.properties key %r — ignored", key)
                        continue
                    section, leaf = target
                    config.setdefault(section, {})[leaf] = val
        except Exception as exc:
            logger.warning("Failed to parse properties file %s: %s", path, exc)
            return {}
        return config

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
        # Resolved suite name applied as the default on every record() that
        # doesn't pass one explicitly. testlookup.suite wins, with
        # testlookup.launch as the documented fallback so users who only set
        # the launch label still get a meaningful run-level suite.
        self._suite_name = (
            ConfigLoader.get(cfg, "reporting.suite_name")
            or ConfigLoader.get(cfg, "reporting.launch_name")
        )
        # testlookup.release / TESTLOOKUP_RELEASE — applied as the default
        # release_name on every session() that doesn't pass one explicitly.
        # The server falls back to the project's default release when this
        # is left blank.
        self._release_name = ConfigLoader.get(cfg, "reporting.release_name")
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
        launch_name: Optional[str] = None,
        suite_name: Optional[str] = None,
        release_name: Optional[str] = None,
    ):
        """
        Async context manager that manages the full session lifecycle:
          __aenter__ → register session with server
          yield      → LiveSession object for recording events
          __aexit__  → flush remaining events + mark session complete
        """
        # Per-session suite overrides the reporter-level default; otherwise
        # inherit testlookup.suite > testlookup.launch resolved in __init__.
        resolved_suite = suite_name if suite_name is not None else self._suite_name
        # Same inheritance rule for release_name — explicit arg wins, otherwise
        # testlookup.release config / TESTLOOKUP_RELEASE env. Server falls
        # back to the project's default release when blank.
        resolved_release = release_name if release_name is not None else self._release_name
        live = await self._create_session(
            build_number=build_number,
            branch=branch,
            commit_hash=commit_hash,
            total_tests=total_tests,
            machine_id=machine_id,
            launch_name=launch_name,
            suite_name=resolved_suite,
            release_name=resolved_release,
        )
        try:
            yield live
        finally:
            await live._shutdown()
            await self._close_session(live.session_id)

    async def _create_session(self, **kwargs) -> "LiveSession":
        """Register a new session with the server and return a LiveSession."""
        # The session's resolved suite is consumed both as a payload field
        # (server stamps LiveSession.suite_name / TestRun.primary_suite_name)
        # and as the LiveSession default for record() calls. Pop early so we
        # can pass it down to LiveSession without leaving it in payload twice.
        suite_for_session = kwargs.pop("suite_name", None)
        payload: dict[str, Any] = {
            "project_id": self._project_id,
            "client_name": self._client_name,
            "framework": self._framework,
            "machine_id": kwargs.pop("machine_id") or socket.gethostname(),
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        if suite_for_session is not None:
            payload["suite_name"] = suite_for_session

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
            suite_name=suite_for_session,
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
        suite_name: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.session_token = session_token
        self.run_id = run_id
        # Default suite applied to every record() event without an explicit
        # suite_name kw — keeps test runs linked to a single user-configured
        # suite identifier without per-test plumbing.
        self._default_suite_name = suite_name
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
        # Inherit the session-level default (resolved from testlookup.suite >
        # testlookup.launch in _create_session) when caller didn't pass one.
        effective_suite = suite_name if suite_name is not None else self._default_suite_name
        if effective_suite: event["suite_name"]  = effective_suite
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
        """POST a batch to the server with retry on transient errors.

        Retryable conditions: connect/read transport errors, HTTP 429
        (admission-gate rate-limit), HTTP 503 (Redis backpressure). On
        429/503 the server's ``Retry-After`` header is honoured exactly;
        otherwise we fall back to exponential backoff. Cumulative retry
        time is capped at ``MAX_RETRY_TOTAL_SECONDS`` so a misconfigured
        project surfaces a hard failure instead of buffering forever.
        """
        payload = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "events": events,
        }
        headers = {"X-Session-Token": self.session_token}
        deadline_started = time.monotonic()

        for attempt in range(1, MAX_RETRIES + 1):
            elapsed = time.monotonic() - deadline_started
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
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                    delay = _compute_next_retry_delay(
                        retry_after_header=retry_after,
                        attempt=attempt,
                        total_elapsed_s=elapsed,
                    )
                    if delay is None or attempt == MAX_RETRIES:
                        logger.error(
                            "Batch POST giving up after HTTP %d "
                            "(attempt=%d events=%d elapsed=%.1fs cap=%.1fs run_id=%s)",
                            resp.status_code, attempt, len(events),
                            elapsed, MAX_RETRY_TOTAL_SECONDS, self.run_id,
                        )
                        self._stats["failed"] += len(events)
                        return
                    logger.warning(
                        "Batch POST throttled: status=%d attempt=%d delay=%.2fs "
                        "retry_after=%r elapsed=%.1fs run_id=%s events=%d",
                        resp.status_code, attempt, delay, retry_after,
                        elapsed, self.run_id, len(events),
                    )
                    await asyncio.sleep(delay)
                    continue

                resp.raise_for_status()
                data = resp.json()
                self._stats["sent"] += data.get("accepted", len(events))
                return

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                delay = _compute_next_retry_delay(
                    retry_after_header=None,
                    attempt=attempt,
                    total_elapsed_s=elapsed,
                )
                if delay is None or attempt == MAX_RETRIES:
                    logger.error(
                        "Batch POST failed after %d attempts "
                        "(events=%d elapsed=%.1fs cap=%.1fs run_id=%s): %s",
                        attempt, len(events), elapsed,
                        MAX_RETRY_TOTAL_SECONDS, self.run_id, exc,
                    )
                    self._stats["failed"] += len(events)
                    return
                logger.warning(
                    "Batch POST transport error: attempt=%d delay=%.2fs "
                    "elapsed=%.1fs run_id=%s events=%d error=%s",
                    attempt, delay, elapsed, self.run_id, len(events), exc,
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
        launch_name: Optional[str] = None,
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
        verify_ssl: Any = None,
    ) -> None:
        cfg = ConfigLoader.load()
        resolved_url = base_url or ConfigLoader.get(cfg, "server.url")
        resolved_key = api_key or ConfigLoader.get(cfg, "auth.api_key") or os.environ.get("TESTLOOKUP_API_KEY")
        resolved_launch = launch_name or ConfigLoader.get(cfg, "reporting.launch_name")
        # Run-level suite identifier: testlookup.suite wins, testlookup.launch
        # is the documented fallback. Stamped on every record() that doesn't
        # supply its own suite_name and sent on the first batch so the server
        # populates LiveSession.suite_name / TestRun.primary_suite_name.
        resolved_suite = (
            ConfigLoader.get(cfg, "reporting.suite_name")
            or resolved_launch
        )
        resolved_build = build_number or ConfigLoader.get(cfg, "ci.build_number")
        resolved_branch = branch or ConfigLoader.get(cfg, "ci.branch")
        resolved_commit = commit_hash or ConfigLoader.get(cfg, "ci.commit_hash")
        resolved_framework = framework if framework != "python" else (
            ConfigLoader.get(cfg, "reporting.framework") or framework
        )

        # TLS resolution. Three states map to httpx's `verify` argument:
        #   True              → use system trust store (default, prod-correct)
        #   False             → skip verification (homelab / self-signed)
        #   "/path/to/ca.pem" → trust this CA bundle (best for homelab)
        # Precedence: constructor verify_ssl > testlookup.ca_cert_path
        # > testlookup.insecure > default True. The `auth.insecure` value
        # comes through as a string from properties files, so a truthy
        # string ("true", "1", "yes") flips verification off.
        if verify_ssl is None:
            ca_path = ConfigLoader.get(cfg, "auth.ca_cert_path")
            insecure_raw = ConfigLoader.get(cfg, "auth.insecure")
            insecure = isinstance(insecure_raw, str) and insecure_raw.strip().lower() in ("1", "true", "yes", "on")
            insecure = insecure or insecure_raw is True
            if ca_path:
                resolved_verify: Any = str(ca_path)
            elif insecure:
                resolved_verify = False
            else:
                resolved_verify = True
        else:
            resolved_verify = verify_ssl
        if not resolved_url:
            raise ValueError(
                "endpoint is required — set testlookup.endpoint in testlookup.properties, "
                "TESTLOOKUP_ENDPOINT/TESTLOOKUP_URL env var, or pass base_url=..."
            )
        if not resolved_key:
            raise ValueError(
                "api_key is required — set testlookup.api.key in testlookup.properties, "
                "TESTLOOKUP_API_KEY env var, or pass api_key=..."
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
        if resolved_build is not None:     self._meta["build_number"] = resolved_build
        if resolved_branch is not None:    self._meta["branch"] = resolved_branch
        if resolved_commit is not None:    self._meta["commit_hash"] = resolved_commit
        if resolved_framework is not None: self._meta["framework"] = resolved_framework
        if total_tests is not None:        self._meta["total_tests"] = total_tests
        if machine_id is not None:         self._meta["machine_id"] = machine_id
        if release_name is not None:       self._meta["release_name"] = release_name
        if resolved_launch is not None:    self._meta["launch_name"] = resolved_launch
        if resolved_suite is not None:     self._meta["suite_name"] = resolved_suite
        if metadata is not None:           self._meta["metadata"] = metadata
        # Cached for record() default — see LiveStream.record below.
        self._default_suite_name = resolved_suite

        if resolved_verify is False:
            logger.warning(
                "LiveStream %s: TLS verification disabled (testlookup.insecure / TESTLOOKUP_INSECURE / verify_ssl=False).",
                self._run_id,
            )

        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": self._api_key},
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=10.0, pool=5.0),
            verify=resolved_verify,
        )

        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._flusher_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._stats = {"sent": 0, "failed": 0}
        self._session_id: Optional[str] = None  # set by the server on first batch
        # Wall-clock ms of the most recent real (non-heartbeat) event we
        # enqueued. Used to suppress heartbeats while the session is busy
        # so we don't add noise to a healthy stream.
        self._last_real_enqueue_ms: float = time.time() * 1_000

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def __aenter__(self) -> "LiveStream":
        self._flusher_task = asyncio.create_task(
            self._flusher_loop(), name=f"testlookup-livestream-{self._run_id[:24]}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"testlookup-heartbeat-{self._run_id[:24]}"
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
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
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
        # Inherit the run-level suite default (testlookup.suite > testlookup.launch)
        # when caller didn't pass an explicit suite_name.
        effective_suite = suite_name if suite_name is not None else self._default_suite_name
        if effective_suite: event["suite_name"]  = effective_suite
        if class_name:    event["class_name"]  = class_name
        if error:         event["error_message"] = error
        if stack_trace:   event["stack_trace"] = stack_trace
        if tags:          event["tags"] = tags
        if metadata:      event["metadata"] = metadata

        self._last_real_enqueue_ms = time.time() * 1_000
        await self._queue.put(event)
        if self._queue.qsize() >= self._batch_size:
            await self._flush_once()

    async def log(self, message: str, level: str = "INFO", metadata: Optional[dict] = None) -> None:
        self._last_real_enqueue_ms = time.time() * 1_000
        await self._queue.put({
            "event_type": "log",
            "test_name": None,
            "status": None,
            "metadata": {"level": level, "message": message, **(metadata or {})},
            "timestamp_ms": int(time.time() * 1_000),
        })

    async def metric(self, name: str, value: float, unit: str = "", metadata: Optional[dict] = None) -> None:
        self._last_real_enqueue_ms = time.time() * 1_000
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

    async def _heartbeat_loop(self) -> None:
        """Periodic driver — wakes every ``HEARTBEAT_INTERVAL`` and asks
        the helper whether to emit. Kept tiny so the testable decision
        logic lives in ``_maybe_emit_heartbeat`` instead of being trapped
        behind ``asyncio.sleep``.
        """
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._maybe_emit_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("LiveStream heartbeat loop error (non-fatal): %s", exc)

    async def _maybe_emit_heartbeat(self) -> bool:
        """Enqueue a ``live_heartbeat`` event if the SDK has been silent
        for at least ``HEARTBEAT_INTERVAL`` seconds. Returns ``True``
        when a heartbeat was actually enqueued, ``False`` when
        suppressed (the session is busy).

        Mirrors the Java SDK's behaviour: bumps the server's Redis
        ``last_event_at`` so the idle-session reaper doesn't kill a
        legitimately-running session during a long inter-test gap.
        We intentionally do NOT update ``_last_real_enqueue_ms`` for
        heartbeats — that timer tracks *real* activity, so heartbeats
        suppressing themselves would defeat the point.
        """
        idle_s = time.time() - (self._last_real_enqueue_ms / 1_000.0)
        if idle_s < HEARTBEAT_INTERVAL:
            return False
        await self._queue.put({
            "event_type": "live_heartbeat",
            "timestamp_ms": int(time.time() * 1_000),
        })
        return True

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
        """POST a batch to the API-key ingest endpoint with retry policy.

        Same retry contract as ``LiveSession._post_batch``: 429/503 are
        retryable with exact ``Retry-After`` honouring, transport errors
        retry with exponential backoff, cumulative retry time capped at
        ``MAX_RETRY_TOTAL_SECONDS``.
        """
        payload: dict[str, Any] = {"run_id": self._run_id, "events": events}
        if self._meta:
            payload["meta"] = self._meta
        deadline_started = time.monotonic()

        for attempt in range(1, MAX_RETRIES + 1):
            elapsed = time.monotonic() - deadline_started
            try:
                resp = await self._http.post("/api/v1/stream/ingest", json=payload)
                if resp.status_code in (401, 403):
                    logger.error(
                        "LiveStream auth failed (%s) — stopping flush: %s",
                        resp.status_code, resp.text,
                    )
                    self._stats["failed"] += len(events)
                    return
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
                    delay = _compute_next_retry_delay(
                        retry_after_header=retry_after,
                        attempt=attempt,
                        total_elapsed_s=elapsed,
                    )
                    if delay is None or attempt == MAX_RETRIES:
                        logger.error(
                            "LiveStream giving up after HTTP %d "
                            "(attempt=%d events=%d elapsed=%.1fs cap=%.1fs run_id=%s)",
                            resp.status_code, attempt, len(events),
                            elapsed, MAX_RETRY_TOTAL_SECONDS, self._run_id,
                        )
                        self._stats["failed"] += len(events)
                        return
                    logger.warning(
                        "LiveStream throttled: status=%d attempt=%d delay=%.2fs "
                        "retry_after=%r elapsed=%.1fs run_id=%s events=%d",
                        resp.status_code, attempt, delay, retry_after,
                        elapsed, self._run_id, len(events),
                    )
                    await asyncio.sleep(delay)
                    continue

                resp.raise_for_status()
                data = resp.json()
                self._stats["sent"] += data.get("accepted", len(events))
                if not self._session_id:
                    self._session_id = data.get("session_id")
                return
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                delay = _compute_next_retry_delay(
                    retry_after_header=None,
                    attempt=attempt,
                    total_elapsed_s=elapsed,
                )
                if delay is None or attempt == MAX_RETRIES:
                    logger.error(
                        "LiveStream POST failed after %d attempts "
                        "(events=%d elapsed=%.1fs cap=%.1fs run_id=%s): %s",
                        attempt, len(events), elapsed,
                        MAX_RETRY_TOTAL_SECONDS, self._run_id, exc,
                    )
                    self._stats["failed"] += len(events)
                    return
                logger.warning(
                    "LiveStream transport error: attempt=%d delay=%.2fs "
                    "elapsed=%.1fs run_id=%s events=%d error=%s",
                    attempt, delay, elapsed, self._run_id, len(events), exc,
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
    group.addoption("--testlookup-launch",  default=os.environ.get("TESTLOOKUP_LAUNCH", ""))


def pytest_configure(config):  # noqa: D401
    """Attach the reporter plugin if configuration is present.

    Resolution order: CLI flags → env vars → testlookup.properties / testlookup.yaml.
    If a config file or env vars are present, the plugin activates without any
    CLI flags — matches ReportPortal's auto-discovery behaviour.
    """
    url     = config.getoption("--testlookup-url",     default="")
    token   = config.getoption("--testlookup-token",   default="")
    project = config.getoption("--testlookup-project", default="")
    build   = config.getoption("--testlookup-build",   default="")
    branch  = config.getoption("--testlookup-branch",  default="")
    launch  = config.getoption("--testlookup-launch",  default="")

    # If CLI flags are incomplete, try ConfigLoader (file + env vars)
    if not (url and token and project):
        cfg = ConfigLoader.load()
        url     = url     or ConfigLoader.get(cfg, "server.url", "")
        token   = token   or ConfigLoader.get(cfg, "auth.token", "") or ConfigLoader.get(cfg, "auth.api_key", "")
        project = project or ConfigLoader.get(cfg, "project.id", "")
        build   = build   or ConfigLoader.get(cfg, "ci.build_number", "")
        branch  = branch  or ConfigLoader.get(cfg, "ci.branch", "")
        launch  = launch  or ConfigLoader.get(cfg, "reporting.launch_name", "")

    if url and token and project:
        plugin = _TestLookupPytestPlugin(
            base_url=url,
            token=token,
            project_id=project,
            build_number=build,
            branch=branch,
            launch_name=launch,
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
        launch_name: str = "",
    ) -> None:
        self._reporter = TestLookupReporter(
            base_url=base_url,
            token=token,
            project_id=project_id,
            framework="pytest",
        )
        self._build_number = build_number or f"pytest-{int(time.time())}"
        self._branch = branch
        self._launch_name = launch_name
        self._live: Optional[LiveSession] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── pytest hooks ──────────────────────────────────────────────────────

    def pytest_sessionstart(self, session):
        self._loop = asyncio.new_event_loop()
        self._live = self._loop.run_until_complete(
            self._reporter._create_session(
                build_number=self._build_number,
                branch=self._branch or None,
                launch_name=self._launch_name or None,
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
