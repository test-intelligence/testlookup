"""The structlog guard must see a call however it is formatted.

``BoundLogger.<level>`` is ``(event, **kw)``. A stdlib-style
``logger.info("x %s", val)`` therefore raises ``TypeError`` at call time — and
when that call sits inside a ``try``/``except``, the feature around it dies
silently. That is exactly how checkpoint restore broke (PR #573): the log line
raised, a blanket ``except`` swallowed it, and every restore returned ``None``
while logging a generic failure.

The guard existed at the time and did not catch it, because it matched with a
**single-line regex** and the offending call was split across four lines. That
blind spot hid ~150 further instances across ``worker/tasks.py``,
``agents/workflow.py`` and the RAG services.

There is a mirror-image trap on the other side. ``worker/tasks.py`` binds BOTH
``logger = logging.getLogger(__name__)`` and ``_slog = structlog.get_logger(...)``.
A guard that decides per FILE — "this module mentions structlog, so every
``logger.*`` call in it must use kwargs" — flags ~140 stdlib calls that are
perfectly correct, and "fixing" those breaks them with the opposite
``TypeError``: stdlib ``Logger._log()`` rejects arbitrary keywords. So the
guard must resolve which NAMES are bound to ``structlog.get_logger`` and judge
only those.

These tests pin the CLASS from both directions: the guard must flag the
multi-line form, the ``exc_info=True`` form, and must NOT fire on stdlib
loggers or on correct kwargs-style calls. Neither formatting nor file-level
co-location may decide whether a latent crash is visible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

quality_gate = pytest.importorskip("quality_gate")


def _violations(tmp_path, monkeypatch, source: str):
    """Run the guard against a synthetic module and return its findings."""
    app_dir = tmp_path / "backend" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "sample.py").write_text(
        'import structlog\nlogger = structlog.get_logger("x")\n\n' + source,
        encoding="utf-8",
    )
    monkeypatch.setattr(quality_gate, "REPO_ROOT", tmp_path)
    return quality_gate._backend_structlog_positional_args()


def test_guard_catches_a_multiline_positional_call(tmp_path, monkeypatch):
    """THE regression. This exact shape shipped a silently-dead feature."""
    found = _violations(tmp_path, monkeypatch, '''
def f(prev_run, stages):
    logger.info(
        "Loaded checkpoint from previous run %s: stages=%s",
        prev_run.id, stages,
    )
''')
    assert len(found) == 1, "a call split across lines must not be invisible"


def test_guard_still_catches_the_single_line_form(tmp_path, monkeypatch):
    found = _violations(tmp_path, monkeypatch, '''
def f(exc):
    logger.warning("thing failed: %s", exc)
''')
    assert len(found) == 1


def test_guard_catches_positional_args_beside_exc_info(tmp_path, monkeypatch):
    """``exc_info=True`` is legitimate, but positional args next to it still
    explode — a keyword being present must not buy the call an exemption."""
    found = _violations(tmp_path, monkeypatch, '''
def f(exc):
    logger.error("ingest failed: %s", exc, exc_info=True)
''')
    assert len(found) == 1


@pytest.mark.parametrize("good", [
    'logger.info("event_name", run_id=run_id)',
    'logger.info("event_name")',
    'logger.warning(\n        "event_name",\n        error=str(exc),\n    )',
    # A percent sign in a kwargs-style message is not interpolation.
    'logger.info("cache_hit_rate_100%", rate=rate)',
])
def test_guard_does_not_fire_on_correct_kwargs_calls(tmp_path, monkeypatch, good):
    found = _violations(tmp_path, monkeypatch, f"def f(run_id=None, exc=None, rate=0):\n    {good}\n")
    assert found == []


def test_guard_ignores_a_stdlib_logger_in_a_dual_binding_module(tmp_path, monkeypatch):
    """The mirror-image trap, and the one that nearly cost 141 correct calls.

    ``worker/tasks.py`` binds a stdlib ``logger`` alongside a structlog
    ``_slog``. Positional %s args on the stdlib one are CORRECT; converting
    them to kwargs raises ``TypeError: Logger._log() got an unexpected keyword
    argument``. A file-level heuristic cannot tell these apart — the guard must
    resolve the binding.
    """
    app_dir = tmp_path / "backend" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "dual.py").write_text(
        "import logging\n"
        "import structlog\n"
        "logger = logging.getLogger(__name__)\n"
        "_slog = structlog.get_logger('worker.tasks')\n"
        "\n"
        "def f(run_id, exc):\n"
        "    logger.info('Persisting run %s', run_id)\n"          # stdlib: fine
        "    _slog.info('run_persisted', run_id=run_id)\n",        # structlog: fine
        encoding="utf-8",
    )
    monkeypatch.setattr(quality_gate, "REPO_ROOT", tmp_path)
    assert quality_gate._backend_structlog_positional_args() == [], (
        "stdlib positional args must not be flagged just because the module "
        "also imports structlog"
    )


def test_guard_still_flags_the_structlog_binding_in_a_dual_module(tmp_path, monkeypatch):
    """...but the structlog name in that same module is still judged."""
    app_dir = tmp_path / "backend" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "dual.py").write_text(
        "import logging\n"
        "import structlog\n"
        "logger = logging.getLogger(__name__)\n"
        "_slog = structlog.get_logger('worker.tasks')\n"
        "\n"
        "def f(run_id):\n"
        "    _slog.info('Persisting run %s', run_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(quality_gate, "REPO_ROOT", tmp_path)
    found = quality_gate._backend_structlog_positional_args()
    assert len(found) == 1


def test_backend_app_is_and_stays_clean(tmp_path, monkeypatch):
    """No baseline, no exceptions: the whole tree must be free of this class.

    An entry appearing here is not a workflow step to accept — it is a latent
    silent failure wherever it sits inside a try/except.
    """
    monkeypatch.undo()
    offenders = quality_gate._backend_structlog_positional_args()
    assert offenders == [], (
        "structlog positional-arg calls reintroduced:\n"
        + "\n".join(f"  {v.path}:{v.line}" for v in offenders)
    )
