"""TestLookup live listener for Robot Framework (W18 DEV-03), Listener API v3.

    robot --listener testlookup_robot_listener.TestLookupListener tests/
    robot --listener "testlookup_robot_listener.TestLookupListener:project=<id>:url=https://testlookup.example.com" tests/

Configuration resolves like the pytest plugin: listener arguments, then
``TESTLOOKUP_*`` environment variables, then ``testlookup.properties`` /
``testlookup.yaml`` (``testlookup_reporter.ConfigLoader``). A missing URL,
credential or project disables the listener with one warning.

Each test is one result when it ends (``end_test``): the test name, its suite's
long name as the suite, PASS/FAIL/SKIP/NOT RUN -> PASSED/FAILED/SKIPPED, the
failure message, tags, duration and start time. Network errors are logged and
swallowed: the listener never fails the Robot run.

Supported: Robot Framework >= 5 (``elapsedtime`` ms) and 7 (``elapsed_time``).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from testlookup_reporter import ConfigLoader, LiveSession, TestLookupReporter, _resolve_credentials

logger = logging.getLogger("testlookup.robot")

_STATUS = {"PASS": "PASSED", "FAIL": "FAILED", "SKIP": "SKIPPED", "NOT RUN": "SKIPPED"}


def _duration_ms(result: Any) -> int:
    elapsed = getattr(result, "elapsed_time", None)
    if elapsed is not None and hasattr(elapsed, "total_seconds"):
        return int(elapsed.total_seconds() * 1000)
    return int(getattr(result, "elapsedtime", 0) or 0)


def _start_ms(result: Any) -> Optional[int]:
    start = getattr(result, "start_time", None)
    if isinstance(start, datetime):
        if start.tzinfo is None:
            start = start.astimezone()  # Robot stamps local time
        return int(start.astimezone(timezone.utc).timestamp() * 1000)
    raw = getattr(result, "starttime", None)  # RF < 7: "20261026 10:00:00.123"
    if isinstance(raw, str) and raw and raw != "N/A":
        try:
            return int(datetime.strptime(raw, "%Y%m%d %H:%M:%S.%f").astimezone().timestamp() * 1000)
        except ValueError:
            return None
    return None


class TestLookupListener:
    """``--listener testlookup_robot_listener.TestLookupListener[:key=value...]``."""

    ROBOT_LISTENER_API_VERSION = 3
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        url: str = "",
        project: str = "",
        api_key: str = "",
        token: str = "",
        build: str = "",
        branch: str = "",
        launch: str = "",
    ) -> None:
        cfg = ConfigLoader.load()
        self._url = url or ConfigLoader.get(cfg, "server.url", "")
        self._project = project or ConfigLoader.get(cfg, "project.id", "")
        self._api_key = api_key or None
        self._token = token or None
        has_credential = bool(self._api_key or self._token) or any(_resolve_credentials(cfg, None, None))
        self.enabled = bool(self._url and has_credential and self._project)
        self._build = build
        self._branch = branch
        self._launch = launch
        self._reporter: Optional[TestLookupReporter] = None
        self._live: Optional[LiveSession] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False
        if not self.enabled:
            logger.warning(
                "[TestLookup] Robot listener disabled: missing URL, API key/token, or project. "
                "Set TESTLOOKUP_ENDPOINT, TESTLOOKUP_API_KEY and TESTLOOKUP_PROJECT, or a testlookup.properties file."
            )

    # ── Listener API v3 ─────────────────────────────────────────────────────

    def start_suite(self, data: Any, result: Any) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        try:
            self._reporter = TestLookupReporter(
                base_url=self._url, token=self._token, api_key=self._api_key, project_id=self._project,
                framework=ConfigLoader.get(ConfigLoader.load(), "reporting.framework") or "robot",
            )
            build = self._build or self._reporter._session_defaults.get("build_number") or f"robot-{int(time.time())}"
            self._loop = asyncio.new_event_loop()
            self._live = self._loop.run_until_complete(self._reporter._create_session(
                build_number=build, branch=self._branch or None,
                launch_name=self._launch or getattr(data, "name", None) or None,
            ))
            logger.info("TestLookup live session started: %s", self._live.session_id)
        except Exception as exc:  # noqa: BLE001 - streaming never fails the run
            logger.error("[TestLookup] Failed to start session: %s", exc)
            self._live = None

    def end_test(self, data: Any, result: Any) -> None:
        if self._live is None or self._loop is None:
            return
        try:
            status = _STATUS.get(str(getattr(result, "status", "")).upper(), "BROKEN")
            message = getattr(result, "message", "") or None
            parent = getattr(data, "parent", None)
            suite = getattr(parent, "longname", None) or getattr(parent, "full_name", None) or getattr(parent, "name", None)
            timing = {k: v for k, v in {"started_at_ms": _start_ms(result)}.items() if v is not None}
            self._loop.run_until_complete(self._live.record(
                test_name=getattr(data, "name", "test"),
                status=status,
                duration_ms=_duration_ms(result),
                suite_name=suite,
                error=message.splitlines()[0] if (message and status != "PASSED") else None,
                stack_trace=message if status == "FAILED" else None,
                tags=[str(t) for t in (getattr(result, "tags", None) or getattr(data, "tags", None) or [])],
                **timing,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[TestLookup] record error: %s", exc)

    def close(self) -> None:
        if self._live is None or self._loop is None or self._reporter is None:
            return
        try:
            self._loop.run_until_complete(self._live._shutdown())
            self._loop.run_until_complete(self._reporter._close_session(self._live.session_id))
            self._loop.run_until_complete(self._reporter.aclose())
            logger.info("TestLookup session finished: sent=%d failed=%d", self._live.stats["sent"], self._live.stats["failed"])
        except Exception as exc:  # noqa: BLE001
            logger.error("[TestLookup] Failed to close session: %s", exc)
        finally:
            self._loop.close()
            self._loop = None
