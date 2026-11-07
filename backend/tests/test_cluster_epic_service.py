"""W36 STRAT-03 — ``cluster_epic_service``: the gating order, the request sent, no Defect row, the status.

The service's own reads (``_project_settings`` / ``_current_cluster`` / ``_members`` / ``_stored_link`` /
``_current_links``) are patched per test; the REAL ``defect_jira_service._file_or_reuse`` runs (its lock, dedupe
and generation reads hit a fake session that answers "nothing"), with the E7.6 ledger in memory and
``_jira_post`` / ``_jira_get`` recorded. The SQL itself is proven on PostgreSQL by
``tests/integration/test_cluster_epic_postgres.py``.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.postgres import AccessAuditLog, Defect, SystemicClusterEpic
from app.services import cluster_epic_service as ces
from app.services import defect_jira_service as svc
from app.services import tool_call_idempotency as tci
from tests.fakes.fake_integrations import adf_problems

PID = uuid.UUID("36a00000-0000-4000-8000-000000000003")
KEY = "0123456789abcdef0123456789abcdef"
CFG = {"enabled": True, "domain": "example.atlassian.net", "email": "bot@example.com", "api_token": "t",
       "default_project_key": "DEF"}
ACTOR = SimpleNamespace(id=uuid.uuid4(), username="qa.eng", full_name="Qa Eng", role="QA_ENGINEER")


class _Result:
    def first(self):
        return None

    def scalar_one_or_none(self):
        return None


class _DB:
    """The filing core's own reads (advisory lock, dedupe, generation) all answer "nothing"."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, *_a, **_kw):
        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1


class _Ledger:
    """The ``_claim`` / ``_finish`` state machine in memory; records every claim's keyword arguments."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.claims: list[dict[str, Any]] = []

    async def claim(self, *, key, **kw):
        self.claims.append({"key": key, **kw})
        row = self.rows.get(key)
        if row is not None and row["status"] == "executed":
            return "executed", dict(row["result"])
        if row is not None and row["status"] != "failed":
            return "executing", {}
        self.rows[key] = {"status": "executing", "result": {}}
        return "claimed", {}

    async def finish(self, *, key, status, result_payload=None, **_kw):
        row = self.rows.setdefault(key, {"status": status, "result": {}})
        row["status"] = status
        if result_payload is not None:
            row["result"] = dict(result_payload)


def _cluster(**over: Any) -> SimpleNamespace:
    values = dict(id=uuid.uuid4(), label="Gateway timeouts", cause_family="networking", size=3, cohesion=0.82,
                  co_failure_runs=4, window_days=60, cluster_key="sfc_001", membership_key=KEY,
                  computed_at=datetime(2026, 11, 5, tzinfo=timezone.utc))
    values.update(over)
    return SimpleNamespace(**values)


MEMBERS = [("fp-a", "test_checkout_total"), ("fp-b", "test_refund"), ("fp-c", None)]


@pytest.fixture
def world(monkeypatch):
    """Opted in, Jira online with an Epic type, a recurring cluster of three, no stored link."""
    state = SimpleNamespace(enabled=True, project_key=None, cluster=_cluster(), members=list(MEMBERS),
                            links=[None], posts=[], gets=[], activity=[], metadata_calls=0, cfg_calls=0,
                            issue_types=["Bug", "Task", "Epic"], metadata_available=True, ledger=_Ledger(),
                            filings=0)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _settings(db, pid):
        return state.enabled, state.project_key

    async def _current(db, pid, key):
        return state.cluster

    async def _members(db, cluster_id):
        return list(state.members)

    async def _stored(db, pid, key):
        return state.links.pop(0) if len(state.links) > 1 else state.links[0]

    async def _cfg(db):
        state.cfg_calls += 1
        return dict(CFG)

    async def _metadata(db, project_id, *, user=None):
        state.metadata_calls += 1
        if not state.metadata_available:
            return {"available": False, "reason": "unreachable", "issue_types": []}
        return {"available": True, "reason": None, "issue_types": list(state.issue_types)}

    async def _post(cfg, path, json):
        state.posts.append((path, json))
        return SimpleNamespace(status_code=201, json=lambda: {"key": "DEF-42"}, text="")

    async def _get(cfg, path, params=None):
        state.gets.append((path, params))
        return SimpleNamespace(status_code=200, json=lambda: {"issues": []}, text="")

    async def _record(db, **kw):
        state.activity.append(kw)

    real_file = svc._file_or_reuse

    async def _counted_file(*args, **kwargs):
        state.filings += 1
        return await real_file(*args, **kwargs)

    monkeypatch.setattr(ces, "_project_settings", _settings)
    monkeypatch.setattr(ces, "_current_cluster", _current)
    monkeypatch.setattr(ces, "_members", _members)
    monkeypatch.setattr(ces, "_stored_link", _stored)
    monkeypatch.setattr(ces, "record_activity", _record)
    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "get_metadata", _metadata)
    monkeypatch.setattr(svc, "_jira_post", _post)
    monkeypatch.setattr(svc, "_jira_get", _get)
    monkeypatch.setattr(svc, "_file_or_reuse", _counted_file)
    monkeypatch.setattr(tci, "_claim", state.ledger.claim)
    monkeypatch.setattr(tci, "_finish", state.ledger.finish)
    return state


async def _create(**kwargs: Any) -> tuple[dict[str, Any], _DB]:
    db = _DB()
    out = await ces.create_epic_for_cluster(db, PID, ACTOR, KEY, **kwargs)
    return out, db


async def _refused(**kwargs: Any) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await _create(**kwargs)
    return caught.value


def _nothing_sent(world: SimpleNamespace) -> bool:
    return world.posts == [] and world.gets == [] and world.filings == 0 and world.activity == []


# ── the gating order ─────────────────────────────────────────────────────────


async def test_the_opt_in_off_beats_offline(world, monkeypatch):
    world.enabled = False
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    refused = await _refused()
    assert (refused.status_code, refused.detail) == (403, "Epic suggestions are off for this project")
    assert world.cfg_calls == 0 and _nothing_sent(world)


async def test_offline_is_the_defect_creates_503_before_the_cluster_is_read(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    world.cluster = None                       # would be a 404 if the order were wrong
    refused = await _refused()
    assert refused.status_code == 503 and refused.detail == svc._REASON_DETAIL["offline_mode"]
    assert world.metadata_calls == 0 and _nothing_sent(world)


async def test_a_cluster_that_is_gone_is_404(world):
    world.cluster = None
    refused = await _refused()
    assert (refused.status_code, refused.detail) == (404, ces.CLUSTER_GONE_DETAIL)
    assert _nothing_sent(world)


async def test_a_cluster_below_three_co_failure_runs_is_not_recurring(world):
    world.cluster = _cluster(co_failure_runs=ces.EPIC_MIN_CO_FAILURE_RUNS - 1)
    world.issue_types = ["Bug"]                # would be no_epic_issue_type if the order were wrong
    refused = await _refused()
    assert refused.status_code == 422 and refused.detail["code"] == "not_recurring"
    assert world.metadata_calls == 0 and _nothing_sent(world)


async def test_exactly_three_co_failure_runs_recurs(world):
    world.cluster = _cluster(co_failure_runs=3)
    out, _db = await _create()
    assert out["created"] is True


async def test_no_epic_issue_type_is_422_before_the_stored_link(world):
    world.issue_types = ["Bug", "Task", "Story"]
    world.links = [SimpleNamespace(membership_key=KEY, jira_key="DEF-1", jira_url="u")]
    refused = await _refused()
    assert refused.status_code == 422 and refused.detail["code"] == "no_epic_issue_type"
    assert _nothing_sent(world)


async def test_unreadable_jira_metadata_is_a_502_not_a_guess(world):
    world.metadata_available = False
    refused = await _refused()
    assert refused.status_code == 502 and "unreachable" in refused.detail
    assert _nothing_sent(world)


async def test_a_stored_link_is_returned_without_a_jira_call(world):
    world.links = [SimpleNamespace(membership_key=KEY, jira_key="DEF-7", jira_url="https://j/browse/DEF-7")]
    out, db = await _create()
    assert out == {"membership_key": KEY, "jira_key": "DEF-7", "jira_url": "https://j/browse/DEF-7",
                   "created": False}
    assert _nothing_sent(world) and world.ledger.claims == [] and db.added == []


# ── the request sent, the link, no Defect ────────────────────────────────────


async def test_the_epic_request_its_claim_and_the_link_without_a_defect(world):
    out, db = await _create()

    signature = ces.epic_signature(KEY)
    assert signature == hashlib.sha256(f"systemic-epic::{KEY}".encode()).hexdigest()[:64]
    claim_key = tci.tool_call_key(tool=tci.JIRA_TICKET_TOOL, scope_id=f"cluster-epic:{PID}",
                                  subject_id=f"{signature}:first")
    ((path, body),) = world.posts
    fields = body["fields"]
    assert path == "/rest/api/3/issue"
    assert fields["issuetype"] == {"name": "Epic"}
    assert fields["project"] == {"key": "DEF"}                     # no request / project key: the default
    assert fields["labels"] == ["testlookup", "systemic-cluster-epic", svc._signature_label(claim_key)]
    assert fields["summary"] == "[TestLookup] Recurring failure cluster: Gateway timeouts"
    assert "assignee" not in fields and adf_problems(fields["description"]) == []
    adf = str(fields["description"])
    assert "Automated defect" not in adf and "Failure message" not in adf     # no defect wording in an epic
    assert '"heading"' not in adf.replace("'", '"')
    assert "Recurring failure cluster detected by TestLookup: Gateway timeouts" in adf
    assert "failed together in 4 runs" in adf and "test_checkout_total" in adf
    assert "Cause family: networking" in adf and "Cohesion: 0.82" in adf and "Window: the last 60 days" in adf
    assert "/failures?tab=groups" in adf
    assert "Filed from TestLookup by qa.eng — nothing is filed automatically." in adf
    assert fields["description"] == ces._epic_adf(ces._epic_parts(world.cluster, [
        "test_checkout_total", "test_refund", "fp-c"], filer="qa.eng"))
    (claim,) = world.ledger.claims
    assert claim["key"] == claim_key and claim["target_type"] == "systemic_cluster"
    assert claim["subject_id"] == f"{signature}:first"
    assert claim["request_payload"] == {"signature": signature, "generation": "first", "jira_project_key": "DEF",
                                        "issue_type": "Epic", "label": svc._signature_label(claim_key)}
    assert out == {"membership_key": KEY, "jira_key": "DEF-42",
                   "jira_url": "https://example.atlassian.net/browse/DEF-42", "created": True}
    (link,) = db.added                                             # the link only: no Defect row, ever
    assert isinstance(link, SystemicClusterEpic) and not any(isinstance(o, Defect) for o in db.added)
    assert (link.project_id, link.membership_key, link.jira_key) == (PID, KEY, "DEF-42")
    assert link.cluster_label == "Gateway timeouts" and link.member_fingerprints == ["fp-a", "fp-b", "fp-c"]
    assert link.created_by == ACTOR.id
    (event,) = world.activity
    assert event["event_type"] == "cluster.epic_created" and event["entity_type"] == "systemic_cluster"
    assert event["entity_id"] == KEY and event["context"]["ticket_key"] == "DEF-42"
    assert event["entity_label"] == "Gateway timeouts"


@pytest.mark.parametrize(
    ("requested", "on_project", "expected"),
    [("REQ", "PRJ", "REQ"), (None, "PRJ", "PRJ"), ("  ", "PRJ", "PRJ"), (None, None, "DEF")],
)
async def test_the_jira_project_key_is_the_request_then_the_project_then_the_default(
    world, requested, on_project, expected,
):
    world.project_key = on_project
    await _create(jira_project_key=requested)
    ((_path, body),) = world.posts
    assert body["fields"]["project"] == {"key": expected}


async def test_an_edited_summary_is_sent_on_one_line_within_255(world):
    await _create(summary="Epic:\n  gateway   timeouts " + "x" * 300)
    ((_path, body),) = world.posts
    summary = body["fields"]["summary"]
    assert summary.startswith("Epic: gateway timeouts x") and len(summary) == 255 and "\n" not in summary


async def test_a_link_committed_by_a_concurrent_request_answers_created_false(world):
    """The second request waited on the signature lock, replayed the first claim, then sees its link."""
    winner = SimpleNamespace(membership_key=KEY, jira_key="DEF-42", jira_url="https://j/browse/DEF-42")
    world.links = [None, winner]
    out, db = await _create()
    assert out["created"] is False and out["jira_key"] == "DEF-42"
    assert db.added == [] and world.activity == []


async def test_an_unknown_outcome_is_409_until_confirm_not_filed_as_for_a_defect(world):
    signature = ces.epic_signature(KEY)
    claim_key = tci.tool_call_key(tool=tci.JIRA_TICKET_TOOL, scope_id=f"cluster-epic:{PID}",
                                  subject_id=f"{signature}:first")
    world.ledger.rows[claim_key] = {"status": "executing", "result": {}}     # an earlier answer was lost
    refused = await _refused()
    assert refused.status_code == 409 and refused.detail["code"] == "jira_outcome_unknown"
    assert refused.detail["jira_label"] == svc._signature_label(claim_key)
    assert world.posts == [] and len(world.gets) == 1                          # searched Jira by the label

    out, db = await _create(confirm_not_filed=True)
    assert out["created"] is True and len(world.posts) == 1
    audit = [o for o in db.added if isinstance(o, AccessAuditLog)]
    assert [a.action for a in audit] == ["jira_defect_confirm_not_filed"]
    assert [type(o) for o in db.added if not isinstance(o, AccessAuditLog)] == [SystemicClusterEpic]


# ── the one-click defaults are unchanged ─────────────────────────────────────

_REAL_FILE_OR_REUSE = svc._file_or_reuse


async def test_file_or_reuse_defaults_send_todays_one_click_request(monkeypatch):
    ledger, posts = _Ledger(), []

    async def _none(db, project_id, signature):
        return None

    async def _post(cfg, path, json):
        posts.append(json)
        return SimpleNamespace(status_code=201, json=lambda: {"key": "QA-9"}, text="")

    monkeypatch.setattr(svc, "find_open_linked_defect", _none)
    monkeypatch.setattr(svc, "_jira_post", _post)
    monkeypatch.setattr(tci, "_claim", ledger.claim)
    monkeypatch.setattr(tci, "_finish", ledger.finish)
    prefill = {"signature": "f" * 16, "summary": "[TestLookup] t", "occurrences": {
        "first_seen": None, "last_seen": None, "failing_runs": 1}, "context": {}, "deep_link": "http://x/runs/1"}
    filing = await _REAL_FILE_OR_REUSE(
        _DB(), PID, ACTOR, prefill, dict(CFG, default_project_key="QA"), jira_project_key=None, issue_type=None,
        assignee=None, extra_comment=None, confirm_not_filed=False,
    )
    claim_key = tci.tool_call_key(tool=tci.JIRA_TICKET_TOOL, scope_id=f"one-click:{PID}", subject_id=f"{'f' * 16}:first")
    label = svc._signature_label(claim_key)
    ((body),) = posts
    assert body["fields"]["labels"] == ["testlookup", "one-click-defect", label]
    assert body["fields"]["issuetype"] == {"name": "Bug"} and body["fields"]["project"] == {"key": "QA"}
    assert body["fields"]["description"] == svc._adf_from_prefill(prefill, None)    # description=None: unchanged
    assert "Automated defect filed from TestLookup for: t" in str(body["fields"]["description"])
    (claim,) = ledger.claims
    assert claim["key"] == claim_key and claim["target_type"] == "failure_signature"
    assert claim["request_payload"] == {"signature": "f" * 16, "generation": "first", "jira_project_key": "QA",
                                        "issue_type": "Bug", "label": label}
    assert (filing.how, filing.issue_key) == ("created", "QA-9")


# ── the prefill and the preview ──────────────────────────────────────────────


async def test_the_prefill_names_the_first_50_members_then_how_many_more(world):
    world.members = [(f"fp-{i:03d}", f"test_case_{i:03d}") for i in range(60)]
    world.cluster = _cluster(label="Gateway\ntimeouts " + "y" * 300, cohesion=None, cause_family=None)
    prefill = await ces.build_epic_prefill(_DB(), PID, KEY)
    assert prefill["summary"].startswith("[TestLookup] Recurring failure cluster: Gateway timeouts y")
    assert len(prefill["summary"]) == 255 and "\n" not in prefill["summary"]
    assert "error_message" not in prefill and "occurrences" not in prefill    # nothing rides the defect shape now
    text = prefill["description"]
    assert text.startswith("Recurring failure cluster detected by TestLookup: Gateway timeouts y")
    assert "- Cause family: unknown" in text and "- Cohesion: unknown" in text
    assert "- Co-failure: the tests failed together in 4 runs" in text and "- Window: the last 60 days" in text
    assert "Member tests (60):" in text and "- test_case_049" in text and "- test_case_050" not in text
    assert "\nand 10 more\n" in text
    assert "View in TestLookup: " + prefill["deep_link"] in text
    assert text.endswith("Filed from TestLookup by a TestLookup user — nothing is filed automatically.")
    assert prefill["member_count"] == 60 and prefill["deep_link"].endswith("/failures?tab=groups")
    assert adf_problems(prefill["adf"]) == []


def _adf_texts(node: Any) -> list[str]:
    """Every text node of an ADF document, in document order."""
    if isinstance(node, dict):
        own = [node["text"]] if node.get("type") == "text" else []
        return own + [t for child in node.get("content") or [] for t in _adf_texts(child)]
    return []


async def test_the_preview_text_is_the_jira_description_line_for_line(world):
    world.members = [(f"fp-{i:03d}", f"test_case_{i:03d}") for i in range(53)]
    prefill = await ces.build_epic_prefill(_DB(), PID, KEY, filer="qa.eng")
    lines = [line.removeprefix("- ") for line in prefill["description"].split("\n") if line]
    adf_lines = _adf_texts(prefill["adf"])
    # the link line is two text nodes in the ADF ("View in TestLookup: " + the linked URL)
    link_at = adf_lines.index("View in TestLookup: ")
    adf_lines[link_at:link_at + 2] = ["".join(adf_lines[link_at:link_at + 2])]
    assert adf_lines == lines
    assert lines[0] == "Recurring failure cluster detected by TestLookup: Gateway timeouts"
    assert lines[-1] == "Filed from TestLookup by qa.eng — nothing is filed automatically."
    assert "and 3 more" in lines


async def test_long_member_names_stop_before_the_names_pass_their_budget(world):
    world.members = [(f"fp-{i}", "test_" + "z" * 1200 + str(i)) for i in range(20)]
    text = (await ces.build_epic_prefill(_DB(), PID, KEY))["description"]
    shown = text.count("\n- test_")
    assert shown == ces.MEMBER_NAMES_MAX_CHARS // 1206 and f"\nand {20 - shown} more\n" in text


async def test_a_member_without_a_name_is_shown_by_its_fingerprint(world):
    text = (await ces.build_epic_prefill(_DB(), PID, KEY))["description"]
    assert "- fp-c" in text and "more\n" not in text


async def test_the_preview_answers_the_contract_shape(world):
    world.project_key = "PRJ"
    out = await ces.preview_epic(_DB(), PID, KEY, actor=ACTOR)
    assert set(out) == {"membership_key", "summary", "description_text", "labels", "jira_project_key",
                        "member_count", "co_failure_runs", "cluster_label"}
    assert out["description_text"].endswith("Filed from TestLookup by qa.eng — nothing is filed automatically.")
    assert "Automated defect" not in out["description_text"]
    assert out["labels"] == ["testlookup", "systemic-cluster-epic"] and out["jira_project_key"] == "PRJ"
    assert (out["member_count"], out["co_failure_runs"], out["cluster_label"]) == (3, 4, "Gateway timeouts")
    assert world.posts == [] and world.gets == [] and world.metadata_calls == 0


async def test_the_preview_refuses_gone_and_not_recurring(world):
    world.cluster = _cluster(co_failure_runs=2)
    with pytest.raises(HTTPException) as weak:
        await ces.preview_epic(_DB(), PID, KEY)
    assert weak.value.status_code == 422 and weak.value.detail["code"] == "not_recurring"
    world.cluster = None
    with pytest.raises(HTTPException) as gone:
        await ces.preview_epic(_DB(), PID, KEY)
    assert gone.value.status_code == 404


# ── the status ───────────────────────────────────────────────────────────────


def _link_row(**over: Any) -> tuple[Any, ...]:
    link = SimpleNamespace(membership_key=KEY, jira_key="DEF-3", jira_url="https://j/browse/DEF-3",
                           cluster_label="Gateway timeouts", created_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    values = {"full_name": "Pat Lead", "username": "pat", "erased_at": None, **over}
    return link, values["full_name"], values["username"], values["erased_at"]


async def test_the_status_offline_asks_jira_nothing(world, monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    async def _links(db, pid):
        return []

    monkeypatch.setattr(ces, "_current_links", _links)
    out = await ces.get_epic_status(_DB(), PID)
    assert out == {"enabled": True, "available": False, "reason": ces.REASON_SENTENCES["offline_mode"],
                   "epic_type_available": None, "min_co_failure_runs": 3, "links": []}
    assert world.metadata_calls == 0 and world.gets == []


@pytest.mark.parametrize(("enabled", "types", "expected", "calls"), [
    (True, ["Bug", "Epic"], True, 1), (True, ["Bug", "Task"], False, 1), (False, ["Epic"], None, 0),
])
async def test_the_status_epic_type_comes_from_the_metadata_path(world, monkeypatch, enabled, types, expected, calls):
    world.enabled, world.issue_types = enabled, types

    async def _links(db, pid):
        return []

    monkeypatch.setattr(ces, "_current_links", _links)
    out = await ces.get_epic_status(_DB(), PID)
    assert (out["enabled"], out["available"], out["reason"]) == (enabled, True, None)
    assert out["epic_type_available"] is expected and world.metadata_calls == calls


async def test_the_status_names_the_filer_through_the_erased_projection(world, monkeypatch):
    rows = [_link_row(), _link_row(full_name=None), _link_row(erased_at=datetime.now(timezone.utc)),
            _link_row(full_name=None, username=None)]

    async def _links(db, pid):
        return rows

    monkeypatch.setattr(ces, "_current_links", _links)
    out = await ces.get_epic_status(_DB(), PID)
    assert [link["created_by_name"] for link in out["links"]] == ["Pat Lead", "pat", "Erased user", None]
    assert set(out["links"][0]) == {"membership_key", "jira_key", "jira_url", "cluster_label", "created_at",
                                    "created_by_name"}
