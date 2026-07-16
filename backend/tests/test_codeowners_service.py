"""Unit tests for CODEOWNERS import + blame-aware path-owner assignment
(Epic 8 US-8.3 / US-8.4).

Pure unit tests — no live DB. A scripted fake session returns SELECT results
in order; ORM rows are plain ``SimpleNamespace`` unless the code under test
constructs a real ``ServiceOwnershipRule``.
"""
from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.postgres import UserRole
from app.services import codeowners_service as cs

_QA_LEAD = UserRole.QA_LEAD.value


# ── helpers ─────────────────────────────────────────────────────────────────


def _scalars(rows):
    res = MagicMock()
    res.scalars = MagicMock(return_value=SimpleNamespace(all=lambda: rows))
    return res


def _all(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _rule(pattern, owner, *, service="CODEOWNERS", priority=0):
    return SimpleNamespace(
        id=uuid.uuid4(),
        match_type="path",
        match_pattern=pattern,
        service_name=service,
        team_name=owner,
        team_contact=owner,
        priority=priority,
        is_active=True,
        created_at=None,
    )


class _ScriptedDB:
    """Returns pre-scripted SELECT results in order; records UPDATE stmts."""

    def __init__(self, selects):
        self._selects = list(selects)
        self._i = 0
        self.updates = []

    async def execute(self, stmt, *a, **k):
        if getattr(stmt, "is_update", False):
            self.updates.append(stmt)
            return MagicMock()
        r = self._selects[self._i]
        self._i += 1
        return r


class _ImportDB:
    """Fake session for import: returns ``existing`` from the scope SELECT,
    records adds / deletes / flush."""

    def __init__(self, existing):
        self.existing = existing
        self.added = []
        self.deleted = []
        self.flushed = False

    async def execute(self, stmt, *a, **k):
        return _scalars(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed = True


# ════════════════════════════════════════════════════════════════════════════
# Parser matrix
# ════════════════════════════════════════════════════════════════════════════


def test_parse_skips_comments_and_blanks():
    text = "# comment\n\n   \nsrc/api/**  @alice\n# another\n"
    entries = cs.parse_codeowners(text)
    assert len(entries) == 1
    assert entries[0].pattern == "src/api/**"
    assert entries[0].owners == ["@alice"]


def test_parse_multi_owner_and_email():
    text = "src/  @org/team @bob carol@example.com\n"
    entries = cs.parse_codeowners(text)
    assert entries[0].owners == ["@org/team", "@bob", "carol@example.com"]
    # trailing slash → **; leading slash stripped in _pattern_to_glob
    assert entries[0].glob == "src/**"


def test_parse_line_without_owner_dropped():
    # A pattern with no owner clears ownership in GitHub — nothing to assign.
    entries = cs.parse_codeowners("orphan/path/**\n")
    assert entries == []


def test_parse_preserves_order_for_last_match_wins():
    text = "*  @default\nsrc/api/**  @api-team\n"
    entries = cs.parse_codeowners(text)
    assert [e.owners[0] for e in entries] == ["@default", "@api-team"]


def test_pattern_to_glob_variants():
    assert cs._pattern_to_glob("/build/") == "build/**"
    assert cs._pattern_to_glob("*.py") == "*.py"
    assert cs._pattern_to_glob("/src/api") == "src/api"


# ════════════════════════════════════════════════════════════════════════════
# Import idempotency + provenance + last-match-wins priority
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_import_creates_path_rules_with_provenance_and_priority():
    pid = uuid.uuid4()
    actor = uuid.uuid4()
    text = "*  @default\nsrc/api/**  @api @backup\n"
    db = _ImportDB(existing=[])

    summary = await db_import(db, pid, text, actor)

    assert summary["rules_created"] == 2
    assert summary["rules_replaced"] == 0
    assert db.flushed is True
    # All imported rows carry the CODEOWNERS provenance marker + match_type path.
    assert all(r.service_name == "CODEOWNERS" for r in db.added)
    assert all(r.match_type == "path" for r in db.added)
    # Later file lines get HIGHER priority (last-match-wins under priority DESC).
    by_pattern = {r.match_pattern: r for r in db.added}
    assert by_pattern["src/api/**"].priority > by_pattern["*"].priority
    # Primary owner in team_name; full list in team_contact.
    assert by_pattern["src/api/**"].team_name == "@api"
    assert by_pattern["src/api/**"].team_contact == "@api @backup"


@pytest.mark.asyncio
async def test_import_replaces_only_codeowners_rows():
    pid = uuid.uuid4()
    # The scope SELECT (service_name == "CODEOWNERS") returns only the two
    # imported rows; a hand-authored rule would never be in this result set.
    prior = [_rule("old/**", "@x"), _rule("older/**", "@y")]
    db = _ImportDB(existing=prior)

    summary = await db_import(db, pid, "src/**  @z\n", uuid.uuid4())

    assert summary["rules_replaced"] == 2
    assert db.deleted == prior  # only the CODEOWNERS-sourced rows deleted
    assert len(db.added) == 1


async def db_import(db, pid, text, actor):
    return await cs.import_codeowners_rules(
        db, project_id=pid, text=text, actor_id=actor,
    )


# ════════════════════════════════════════════════════════════════════════════
# @handle → User resolution
# ════════════════════════════════════════════════════════════════════════════


def test_owner_handle_for_rule():
    assert cs.owner_handle_for_rule(_rule("a/**", "@alice")) == "alice"
    # team handle has no single user
    assert cs.owner_handle_for_rule(_rule("a/**", "@org/team")) is None
    # bare email
    assert cs.owner_handle_for_rule(_rule("a/**", "bob@x.com")) == "bob@x.com"


@pytest.mark.asyncio
async def test_resolve_handles_to_users_by_username_and_email():
    U = uuid.uuid4()
    C = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_all([
        SimpleNamespace(id=U, username="alice", email="alice@x.com"),
        SimpleNamespace(id=C, username="carol", email="carol@z.com"),
    ]))
    # "alice" resolves by username; "carol@z.com" by email; "bob@y.com" unmatched.
    mapping = await cs.resolve_handles_to_users(db, {"alice", "carol@z.com", "bob@y.com"})
    assert mapping["alice"] == U
    assert mapping["carol@z.com"] == C
    assert "bob@y.com" not in mapping  # unmatched → absent → caller skips


def test_match_path_rule_priority_first():
    rules = [_rule("src/api/**", "@api", priority=5), _rule("*", "@default", priority=0)]
    matched = cs.match_path_rule("src/api/foo.py", rules)
    assert matched.team_name == "@api"


# ════════════════════════════════════════════════════════════════════════════
# Fetch (mocked GitHub Contents API + offline gate + SSRF)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fetch_offline_gate(monkeypatch):
    from app.services import github_checks_service as gh
    monkeypatch.setattr(gh, "_post_allowed", AsyncMock(return_value=False))
    text, detail = await cs.fetch_codeowners_text(AsyncMock(), uuid.uuid4())
    assert text is None
    assert detail == "offline_or_flag_off"


@pytest.mark.asyncio
async def test_fetch_ssrf_block(monkeypatch):
    from app.services import github_checks_service as gh
    from app.services import secret_service
    monkeypatch.setattr(gh, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(gh, "get_integration", AsyncMock(return_value=SimpleNamespace(
        enabled=True, api_base_url="https://ghe.local", repo_owner="o", repo_name="r",
    )))
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value="pat"))
    monkeypatch.setattr(gh, "_ssrf_block_reason", AsyncMock(return_value="blocked_target:127.0.0.1"))
    text, detail = await cs.fetch_codeowners_text(AsyncMock(), uuid.uuid4())
    assert text is None
    assert detail.startswith("blocked_unsafe_target:")


@pytest.mark.asyncio
async def test_fetch_success_decodes_base64(monkeypatch):
    from app.services import github_checks_service as gh
    from app.services import resilience, secret_service
    monkeypatch.setattr(gh, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(gh, "get_integration", AsyncMock(return_value=SimpleNamespace(
        enabled=True, api_base_url="https://api.github.com", repo_owner="o", repo_name="r",
    )))
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value="pat"))
    monkeypatch.setattr(gh, "_ssrf_block_reason", AsyncMock(return_value=None))

    body = base64.b64encode(b"src/**  @alice\n").decode()
    resp = SimpleNamespace(status_code=200, content=b"x", json=lambda: {"content": body})
    monkeypatch.setattr(resilience, "async_retry", AsyncMock(return_value=resp))

    text, detail = await cs.fetch_codeowners_text(AsyncMock(), uuid.uuid4())
    assert text == "src/**  @alice\n"
    assert detail == "fetched:.github/CODEOWNERS"


@pytest.mark.asyncio
async def test_fetch_404_then_found(monkeypatch):
    from app.services import github_checks_service as gh
    from app.services import resilience, secret_service
    monkeypatch.setattr(gh, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(gh, "get_integration", AsyncMock(return_value=SimpleNamespace(
        enabled=True, api_base_url="https://api.github.com", repo_owner="o", repo_name="r",
    )))
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value="pat"))
    monkeypatch.setattr(gh, "_ssrf_block_reason", AsyncMock(return_value=None))

    body = base64.b64encode(b"docs  @docs\n").decode()
    resp_404 = SimpleNamespace(status_code=404, content=b"", json=lambda: {})
    resp_ok = SimpleNamespace(status_code=200, content=b"x", json=lambda: {"content": body})
    monkeypatch.setattr(resilience, "async_retry", AsyncMock(side_effect=[resp_404, resp_ok]))

    text, detail = await cs.fetch_codeowners_text(AsyncMock(), uuid.uuid4())
    assert text == "docs  @docs\n"
    assert detail == "fetched:CODEOWNERS"


# ════════════════════════════════════════════════════════════════════════════
# Coverage
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_compute_coverage_over_locatable():
    pid = uuid.uuid4()
    rules = [_rule("src/api/**", "@api")]
    sample = [
        SimpleNamespace(stack_trace='File "src/api/x.py", line 3, in f', error_message=None),
        SimpleNamespace(stack_trace='File "src/web/y.py", line 5, in g', error_message=None),
        SimpleNamespace(stack_trace=None, error_message=None),  # non-locatable
    ]
    db = _ScriptedDB([_scalars(rules), _all(sample)])
    cov = await cs.compute_coverage(db, pid)
    assert cov["path_rules"] == 1
    assert cov["located"] == 2      # the two python frames
    assert cov["matched"] == 1      # only src/api/x.py matches
    assert cov["coverage_pct"] == 50.0


@pytest.mark.asyncio
async def test_compute_coverage_no_rules_short_circuits():
    db = _ScriptedDB([_scalars([])])
    cov = await cs.compute_coverage(db, uuid.uuid4())
    assert cov == {
        "path_rules": 0, "codeowners_rules": 0, "sampled": 0, "located": 0,
        "matched": 0, "coverage_pct": 0.0, "lookback_days": 30,
    }


# ════════════════════════════════════════════════════════════════════════════
# Assignment precedence (US-8.4)
# ════════════════════════════════════════════════════════════════════════════


def _assign_selects(*, failures, project=(None, None), pool=None, primary=None,
                    suite_owners=None, path_rules=None, users=None):
    """Build the scripted SELECT sequence for
    ``assign_failed_tests_to_suite_owners`` with the US-8.4 path-rule +
    handle-map queries folded in at the right positions."""
    seq = [
        _all(failures),
        SimpleNamespace(first=lambda: project),
        _all(pool or []),
        SimpleNamespace(scalar_one_or_none=lambda: primary),
        _all(suite_owners or []),
        _scalars(path_rules or []),
    ]
    if path_rules:
        seq.append(_all(users or []))
    return seq


@pytest.mark.asyncio
async def test_path_owner_beats_qa_pool():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    U = uuid.uuid4()
    lead = uuid.uuid4()
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None,
        test_fingerprint="fp", stack_trace='File "src/api/x.py", line 1, in f',
        error_message=None,
    )]
    selects = _assign_selects(
        failures=failures,
        pool=[SimpleNamespace(user_id=lead, role=_QA_LEAD)],
        path_rules=[_rule("src/api/**", "@alice")],
        users=[SimpleNamespace(id=U, username="alice", email="alice@x.com")],
    )
    db = _ScriptedDB(selects)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["path_owner"] == 1
    assert counts["assigned"] == 1
    # One UPDATE issued (to the path owner U, not the QA-lead pool member —
    # proven by path_owner==1 firing before the pool branch).
    assert len(db.updates) == 1
    assert U != lead


@pytest.mark.asyncio
async def test_explicit_suite_owner_wins_over_path_owner():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    owner = uuid.uuid4()
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None,
        test_fingerprint="fp", stack_trace='File "src/api/x.py", line 1, in f',
        error_message=None,
    )]
    selects = _assign_selects(
        failures=failures,
        suite_owners=[SimpleNamespace(suite_name="Smoke", owner_user_id=owner)],
        path_rules=[_rule("src/api/**", "@alice")],
        users=[SimpleNamespace(id=uuid.uuid4(), username="alice", email="a@x.com")],
    )
    db = _ScriptedDB(selects)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    # Explicit owner short-circuits before the path-owner branch runs.
    assert counts["path_owner"] == 0
    assert counts["assigned"] == 1


@pytest.mark.asyncio
async def test_unresolvable_handle_skips_to_pool():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    lead = uuid.uuid4()
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None,
        test_fingerprint="fp", stack_trace='File "src/api/x.py", line 1, in f',
        error_message=None,
    )]
    # Path rule matches but resolves to a team handle → no single user → skip.
    selects = _assign_selects(
        failures=failures,
        pool=[SimpleNamespace(user_id=lead, role=_QA_LEAD)],
        path_rules=[_rule("src/api/**", "@org/team")],
    )
    db = _ScriptedDB(selects)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["path_owner"] == 0
    assert counts["assigned"] == 1  # fell through to the QA-lead pool


@pytest.mark.asyncio
async def test_non_locatable_falls_through_to_pool():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    lead = uuid.uuid4()
    # Java-style trace → locate_in_trace returns None (deliberately unlocated).
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None,
        test_fingerprint="fp",
        stack_trace="at com.app.Foo.bar(Foo.java:42)",
        error_message=None,
    )]
    selects = _assign_selects(
        failures=failures,
        pool=[SimpleNamespace(user_id=lead, role=_QA_LEAD)],
        path_rules=[_rule("src/api/**", "@alice")],
        users=[SimpleNamespace(id=uuid.uuid4(), username="alice", email="a@x.com")],
    )
    db = _ScriptedDB(selects)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["path_owner"] == 0
    assert counts["assigned"] == 1  # QA-lead pool


@pytest.mark.asyncio
async def test_already_assigned_skips_path_resolution():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )
    failures = [SimpleNamespace(
        id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=uuid.uuid4(),
        test_fingerprint="fp", stack_trace='File "src/api/x.py", line 1, in f',
        error_message=None,
    )]
    selects = _assign_selects(
        failures=failures,
        path_rules=[_rule("src/api/**", "@alice")],
        users=[SimpleNamespace(id=uuid.uuid4(), username="alice", email="a@x.com")],
    )
    db = _ScriptedDB(selects)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["already_assigned"] == 1
    assert counts["path_owner"] == 0
    assert db.updates == []  # NULL-only idempotency preserved


# ════════════════════════════════════════════════════════════════════════════
# Read-time reason derivation (inbox "via CODEOWNERS")
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_codeowners_reasons_for_rows_labels_path_owner():
    pid = uuid.uuid4()
    U = uuid.uuid4()
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(), project_id=pid, assigned_to_user_id=U,
            stack_trace='File "src/api/x.py", line 1, in f', error_message=None,
        ),
        SimpleNamespace(  # assigned to someone else → no label
            id=uuid.uuid4(), project_id=pid, assigned_to_user_id=uuid.uuid4(),
            stack_trace='File "src/api/y.py", line 1, in g', error_message=None,
        ),
    ]
    db = _ScriptedDB([
        _scalars([_rule("src/api/**", "@alice")]),
        _all([SimpleNamespace(id=U, username="alice", email="a@x.com")]),
    ])
    reasons = await cs.codeowners_reasons_for_rows(db, rows)
    assert reasons[rows[0].id] == "via CODEOWNERS: src/api/**"
    assert rows[1].id not in reasons
