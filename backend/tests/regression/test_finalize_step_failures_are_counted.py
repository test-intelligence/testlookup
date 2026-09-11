"""A failing finalize post-step must leave a signal, not just a log line.

Re-audit finding H5. ``finalize_run`` runs its post-steps -- suite membership,
canonical sync, deletion reconcile, failed-test assignment, auto-tagging,
quarantine tagging, release linking, commit range, the activity ledger -- each
in its own session, so one failure cannot poison the next. That isolation is
right. It is also exactly what hid failures: the except branch rolled back and
wrote a warning, and nothing else. A step could fail on every run, for weeks,
and the only trace was a log line nobody was paged for. The project whose
canonical cases had silently stopped updating looked identical to a healthy
one.

These tests drive the real runner and read the counter back -- the same split
as ``test_declared_metrics_are_emitted.py``: the quality gate proves a call
site exists, only running it proves the call site runs.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import textwrap

import pytest

pytest.importorskip("app.core.metrics")

from app.core import metrics  # noqa: E402
from app.services import ingestion_pipeline  # noqa: E402

pytestmark = pytest.mark.regression

ALERTS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"
)


def _failures(step: str) -> float:
    return metrics.finalize_step_failures_total.labels(step=step)._value.get()


class _Session:
    """Just enough of an AsyncSession for the runner: commit and rollback."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.info: dict = {}

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def session(monkeypatch):
    holder = {}

    class _Ctx:
        async def __aenter__(self):
            holder["db"] = _Session()
            return holder["db"]

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(ingestion_pipeline, "AsyncSessionLocal", lambda: _Ctx())
    return holder


# ── The signal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_step_is_counted_under_its_own_name(session):
    before = _failures("canonical_sync")

    async def _boom(_db):
        raise RuntimeError("canonical sync blew up")

    ok = await ingestion_pipeline._run_isolated_step("canonical_sync", _boom)

    assert ok is False
    assert _failures("canonical_sync") == before + 1, (
        "a failed finalize step left no metric — it can fail on every run and "
        "nothing can graph or alert on it"
    )
    assert session["db"].rolled_back is True


@pytest.mark.asyncio
async def test_a_successful_step_is_not_counted(session):
    before = _failures("auto_tagging")

    async def _fine(_db):
        return None

    ok = await ingestion_pipeline._run_isolated_step("auto_tagging", _fine)

    assert ok is True
    assert _failures("auto_tagging") == before
    assert session["db"].committed is True


@pytest.mark.asyncio
async def test_one_steps_failure_does_not_count_against_another(session):
    """The label is what makes the alert say WHICH step is broken."""
    before_ledger = _failures("activity_ledger")
    before_release = _failures("release_linking")

    async def _boom(_db):
        raise RuntimeError("ledger down")

    await ingestion_pipeline._run_isolated_step("activity_ledger", _boom)

    assert _failures("activity_ledger") == before_ledger + 1
    assert _failures("release_linking") == before_release


@pytest.mark.asyncio
async def test_isolation_still_holds(session):
    """Counting must not turn a contained failure into a propagated one."""

    async def _boom(_db):
        raise ValueError("contained")

    # Must not raise: the next step still has to run.
    await ingestion_pipeline._run_isolated_step("suite_sync", _boom)


# ── Every step goes through the counted runner ───────────────────────────


def _step_calls():
    source = textwrap.dedent(inspect.getsource(ingestion_pipeline.finalize_run))
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_isolated"
        ):
            calls.append(node)
    return tree, calls


def test_finalize_run_routes_every_step_through_the_counted_runner():
    """A nested copy of the runner would count nothing.

    The runner used to be a closure inside finalize_run. It is module-level
    now; if someone reintroduces a local ``def _run_isolated`` it silently
    shadows the counted one, and every step goes dark again.
    """
    tree, calls = _step_calls()

    local_defs = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_run_isolated"
    ]
    assert not local_defs, (
        "finalize_run defines its own _run_isolated again, shadowing the "
        "module-level runner that counts failures"
    )

    aliases = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_run_isolated" for t in n.targets)
        and isinstance(n.value, ast.Name)
        and n.value.id == "_run_isolated_step"
    ]
    assert aliases, "_run_isolated no longer points at the counted runner"


def test_every_step_names_itself():
    """An unnamed or computed step name would land every failure in one bucket."""
    _tree, calls = _step_calls()
    assert len(calls) >= 9, (
        f"found {len(calls)} finalize steps; expected at least the nine this "
        "counter was written for — the scan may be broken"
    )
    names = []
    for call in calls:
        first = call.args[0] if call.args else None
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            "a finalize step passes a non-literal name, so its failures cannot "
            "be told apart on the dashboard"
        )
        names.append(first.value)
    assert len(names) == len(set(names)), (
        f"two finalize steps share a name, so their failures merge: {names}"
    )


# ── Someone is told ──────────────────────────────────────────────────────


def test_an_alert_fires_on_the_counter():
    """A metric with no alert is a graph nobody opens during an incident."""
    if not ALERTS.exists():
        pytest.skip("alert rules not present")
    text = ALERTS.read_text(encoding="utf-8")
    assert "TestLookupFinalizeStepFailing" in text
    assert "testlookup_finalize_step_failures_total" in text
    assert "by (step)" in text, (
        "the alert must group by step, or it cannot say which one is failing"
    )
