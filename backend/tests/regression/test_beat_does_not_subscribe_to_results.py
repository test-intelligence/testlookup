"""Celery beat must never subscribe to task results.

JR-03 / TL-2026-09-19-01-004. Beat is fire-and-forget, but
``Celery.send_task`` (celery 5.6.3, ``app/base.py``) does::

    ignore_result = options.pop('ignore_result', False)
    ...
    if not ignore_result:
        self.backend.on_task_call(P, task_id)

With the Redis result backend, ``on_task_call`` starts a **result consumer** --
a pub/sub subscription per dispatched task -- inside the long-lived beat process.
They accumulate until the backend connection cannot be re-established and beat
dies::

    celery.beat.SchedulingError: Couldn't apply scheduled task
    relay-pending-notification-deliveries: Retry limit exceeded while trying to
    reconnect to the Celery result store backend. The Celery application must be
    restarted.

**The failure mode is what makes this worth a test.** After that point beat keeps
logging "Sending due task" while the publish never lands, and the container still
reports healthy. Measured on the local stack on 2026-09-19: the worker received
**zero** tasks for 20+ minutes, every queue sat at depth 0, and 20
``run_downstream_outbox`` rows stayed ``pending`` with ``attempts=0`` -- no AI
pipeline, no webhooks, no notifications, no relays.

It presents exactly like a broken outbox relay, and it is not: invoking
``claim_downstream_dispatches`` directly claimed all 20 rows and transitioned them
correctly. An hour was nearly spent filing "the AI pipeline never auto-triggers"
as an S2 product defect against what was actually a dead scheduler.

Two details that make the obvious fixes wrong:

* ``conf.task_ignore_result`` is **not** consulted by ``send_task`` -- the flag is
  read from the per-call ``options`` only. Setting the config would change nothing.
* Beat reaches ``send_task`` rather than ``Task.apply_async`` because the task is
  not registered in the beat process, so a task-level ``ignore_result`` attribute
  is not consulted either.

So the option has to live on the schedule entry, and it is injected in a loop
rather than typed into all 45 entries -- a per-entry option is a rule the author
of entry 46 has to remember, and the symptom is silence.
"""

from __future__ import annotations

import pytest

pytest.importorskip("celery")

from app.worker.celery_app import celery_app  # noqa: E402


class TestEveryScheduleEntryIgnoresResults:
    def test_there_are_schedule_entries_to_check(self):
        # Without this the suite below passes vacuously if beat_schedule moves.
        assert len(celery_app.conf.beat_schedule) > 20

    def test_no_entry_subscribes_to_its_result(self):
        offenders = sorted(
            name
            for name, entry in celery_app.conf.beat_schedule.items()
            if not entry.get("options", {}).get("ignore_result")
        )
        assert offenders == [], (
            "these beat entries would make beat open a Redis pub/sub result "
            "subscription per dispatch; they accumulate until beat's result "
            "backend connection dies and every periodic task silently stops"
        )

    def test_the_injection_covers_new_entries_automatically(self):
        # The loop runs at import over whatever is in beat_schedule, so an entry
        # added later is covered without its author knowing this rule exists.
        # If someone replaces the loop with 45 hand-written options, this still
        # passes -- but the test above then starts failing the moment entry 46
        # arrives, which is the point.
        assert all(
            "options" in entry for entry in celery_app.conf.beat_schedule.values()
        )

    def test_pre_existing_options_are_preserved(self):
        # Two entries already carried `queue`. A naive `entry["options"] = {...}`
        # would have silently dropped their routing.
        routed = {
            name: entry["options"]
            for name, entry in celery_app.conf.beat_schedule.items()
            if entry.get("options", {}).get("queue")
        }
        assert routed, "expected at least one entry to pin its queue"
        for name, options in routed.items():
            assert options["ignore_result"] is True, name
            assert options["queue"], f"{name} lost its queue"


class TestTheCeleryBehaviourThisReliesOn:
    """Pin the upstream contract, so a Celery upgrade that changes it is caught.

    If a future Celery reads ``conf.task_ignore_result`` here, or stops honouring
    the per-call option, the fix above becomes a no-op and beat would start dying
    again with no test failing. So assert the mechanism, not just our config.
    """

    def test_send_task_reads_ignore_result_from_the_call_options(self):
        import inspect

        from celery.app.base import Celery

        src = inspect.getsource(Celery.send_task)
        assert "options.pop('ignore_result'" in src or \
               'options.pop("ignore_result"' in src, (
            "celery.send_task no longer reads ignore_result from the per-call "
            "options -- re-derive how beat avoids subscribing to results"
        )

    def test_the_result_subscription_is_gated_on_that_flag(self):
        import inspect

        from celery.app.base import Celery

        src = inspect.getsource(Celery.send_task)
        lines = [line.strip() for line in src.splitlines()]
        gate = next(
            (i for i, line in enumerate(lines) if line == "if not ignore_result:"),
            None,
        )
        assert gate is not None, "the `if not ignore_result:` gate is gone"
        # on_task_call must be inside that branch, not unconditional.
        assert "on_task_call" in lines[gate + 1], (
            "backend.on_task_call is no longer guarded by ignore_result; beat "
            "would subscribe to every result regardless of the schedule option"
        )
