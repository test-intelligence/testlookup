from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_compose_queues.py"
SPEC = importlib.util.spec_from_file_location("verify_compose_queues", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class FakeResult:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def get(self, *, timeout, propagate):
        assert timeout > 0 and propagate is True
        if self.error:
            raise self.error
        return self.value


class FakeInspect:
    def active_queues(self):
        return {"worker-a": [{"name": "default"}, {"name": "critical"}]}


class FakeControl:
    def inspect(self, *, timeout):
        assert 0 < timeout <= 5
        return FakeInspect()


class FakeApp:
    def __init__(self, results=None, send_errors=None):
        self.results, self.send_errors, self.sent = results or {}, send_errors or {}, []
        self.control = FakeControl()
        self.conf = SimpleNamespace(
            broker_url="redis://:broker-secret@redis:6379/0",
            result_backend="redis://:result-secret@redis:6379/1",
        )

    def send_task(self, name, *, args, queue, task_id):
        self.sent.append((name, queue, task_id))
        if queue in self.send_errors:
            raise self.send_errors[queue]
        return self.results.get(queue, FakeResult({
            "correlation_id": args[0],
            "routing_key": queue,
            "worker": "worker-a",
        }))


def test_shard_count_expands_contract():
    assert probe.queues_for(explicit=[], shard_count=2) == [
        "default", "critical", "ingestion", "ai_analysis", "agent_children",
        "ingestion.shard.0", "ingestion.shard.1",
    ]


@pytest.mark.parametrize("explicit,shards", [(["default"], 0), (["default", "default"], None), ([], None), ([], -1)])
def test_invalid_contracts_fail_closed(explicit, shards):
    with pytest.raises(ValueError):
        probe.queues_for(explicit=explicit, shard_count=shards)


def test_success_routes_every_queue_and_records_attribution(tmp_path):
    app, evidence = FakeApp(), tmp_path / "nested" / "evidence.json"
    queues = ["default", "critical", "agent_children"]
    assert probe.probe_queues(app, topology="release", queues=queues, timeout=30,
                              evidence_path=evidence, source_sha="abc123") == 0
    document = json.loads(evidence.read_text())
    assert document["result"] == "passed"
    assert document["expected_queues"] == queues
    assert all(row["status"] == "passed" for row in document["queues"])
    assert document["queues"][0]["workers"] == ["worker-a"]
    assert [item[1] for item in app.sent] == queues
    assert not list(evidence.parent.glob(f".{evidence.name}.*"))


def test_failures_are_complete_and_credentials_are_sanitized(tmp_path):
    broker = "redis://:broker-secret@redis:6379/0"
    app = FakeApp(
        results={"default": FakeResult(error=RuntimeError(f"failed via {broker}"))},
        send_errors={"critical": RuntimeError("redis://user:password@redis:6379/0 unavailable")},
    )
    evidence = tmp_path / "failed.json"
    assert probe.probe_queues(app, topology="dev", queues=["default", "critical"], timeout=10,
                              evidence_path=evidence, source_sha="deadbeef") == 1
    text, document = evidence.read_text(), json.loads(evidence.read_text())
    assert document["result"] == "failed"
    assert {row["status"] for row in document["queues"]} == {"send_error", "delivery_error"}
    assert "broker-secret" not in text and "password" not in text


def test_unexpected_result_fails_closed(tmp_path):
    evidence = tmp_path / "unexpected.json"
    app = FakeApp(results={"default": FakeResult(value={
        "correlation_id": "wrong", "routing_key": "default", "worker": "worker-a",
    })})
    assert probe.probe_queues(app, topology="lite", queues=["default"], timeout=5,
                              evidence_path=evidence, source_sha="abc") == 1
    assert json.loads(evidence.read_text())["queues"][0]["status"] == "unexpected_result"
