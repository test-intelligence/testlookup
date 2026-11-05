"""The Celery worker and beat log through the API's redacting pipeline.

Only ``app.main`` called ``configure_logging()``. No worker or beat process imports ``app.main``, so their log lines
went through structlog's unconfigured defaults and Celery's own handlers: the privacy redaction processor
(``_privacy_redaction``: e-mail addresses, secrets, connection strings) never ran on them, and ``LOG_FORMAT=json``
was ignored (found writing W32's log-store guidance, 2026-11-05). ``celery_app`` now configures logging from Celery's
``setup_logging`` signal, which Celery sends in the worker and in beat before either logs.
"""
from __future__ import annotations

import logging

import pytest
import structlog

pytestmark = pytest.mark.regression


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    config = structlog.get_config()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**config)


def test_celery_sends_setup_logging_to_the_apps_configuration():
    from celery.signals import setup_logging

    from app.worker import celery_app as module

    receivers = [ref() for _key, ref in setup_logging.receivers]
    assert module._configure_structured_logging in receivers, "no setup_logging receiver: Celery configures logging itself"


def test_a_worker_log_line_is_redacted(restore_logging, capsys):
    from celery.signals import setup_logging

    import app.worker.celery_app  # noqa: F401 -- connects the receiver

    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
    setup_logging.send(sender=None, loglevel=logging.INFO, logfile=None, format="", colorize=False)

    email = "zqxw.worker" + "@example.org"
    secret = "sk-" + "live-" + "zqxwzqxwzqxwzqxw"
    structlog.get_logger("app.worker.tasks").warning(f"digest failed for {email}", token=secret)
    logging.getLogger("celery.worker").warning("connection string postgres://app:zqxwpassword@db:5432/x")

    out = capsys.readouterr().out
    assert "digest failed for" in out and "connection string" in out, out
    for leaked in (email, secret, "zqxwpassword"):
        assert leaked not in out, f"{leaked!r} reached the worker's log unredacted:\n{out}"
