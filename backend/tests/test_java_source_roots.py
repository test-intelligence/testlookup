"""FAIL-08: Java (JUnit / TestNG / Kotlin) frames located through a project's
source roots, wherever TestLookup locates a failure.

A JVM frame names a class and a file, never a path:
``at com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)``. With the project's
roots (``[{prefix: "com.acme.pay", root: "payments/src/test/java"}, {prefix: "",
root: "src/test/java"}]``) it becomes
``payments/src/test/java/com/acme/pay/CheckoutTest.java:42`` for the CODEOWNERS
coverage badge, the ingest-time path-owner assignment, the inbox "via
CODEOWNERS" reason and the check-run annotations. With no roots every answer is
exactly the pre-FAIL-08 one (Java unlocated, never guessed).

Contract: ``docs/deferred-scope-2026-10-13/work/W07-contract.md`` §2.8.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.codeowners_service as cs
import app.services.github_checks_service as gh
from app.models.postgres import UserRole

ROOTS = [
    {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
    {"prefix": "", "root": "src/test/java"},
]

JUNIT_TRACE = (
    "java.lang.AssertionError: expected:<200> but was:<500>\n"
    "\tat org.junit.Assert.fail(Assert.java:89)\n"
    "\tat org.junit.Assert.assertEquals(Assert.java:118)\n"
    "\tat java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)\n"
    "\tat java.base/java.lang.reflect.Method.invoke(Method.java:566)\n"
    "\tat com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)\n"
    "\tat org.junit.runners.model.FrameworkMethod$1.runReflectiveCall(FrameworkMethod.java:59)\n"
)


def _frame(text: str) -> str:
    return f"java.lang.IllegalStateException: boom\n\tat {text}\n"


# ── the locator ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "frame, expected",
    [
        ("com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)",
         ("payments/src/test/java/com/acme/pay/CheckoutTest.java", 42)),
        ("com.acme.other.X.y(X.java:7)", ("src/test/java/com/acme/other/X.java", 7)),
        # nested class and lambda: the FILE comes from the frame
        ("com.acme.pay.Outer$Inner.m(Outer.java:9)",
         ("payments/src/test/java/com/acme/pay/Outer.java", 9)),
        ("com.acme.pay.CheckoutTest.lambda$pays$0(CheckoutTest.java:12)",
         ("payments/src/test/java/com/acme/pay/CheckoutTest.java", 12)),
        ("com.acme.pay.CheckoutTest.<init>(CheckoutTest.java:5)",
         ("payments/src/test/java/com/acme/pay/CheckoutTest.java", 5)),
        # Kotlin: the class is FooTestKt, the file FooTest.kt
        ("com.acme.FooTestKt.t(FooTest.kt:3)", ("src/test/java/com/acme/FooTest.kt", 3)),
        # the default package: root + file
        ("CheckoutTest.pays(CheckoutTest.java:3)", ("src/test/java/CheckoutTest.java", 3)),
    ],
)
def test_a_frame_is_placed_under_its_root(frame, expected):
    assert gh.locate_java_frame(_frame(frame), ROOTS) == expected
    assert gh.locate_in_trace(_frame(frame), java_roots=ROOTS) == expected


def test_the_innermost_non_vendor_frame_wins():
    # org.junit... and java.base/... frames sit above the test's own frame.
    assert gh.locate_in_trace(JUNIT_TRACE, java_roots=ROOTS) == (
        "payments/src/test/java/com/acme/pay/CheckoutTest.java", 42,
    )


def test_caused_by_sections_are_scanned_too():
    trace = (
        "org.opentest4j.AssertionFailedError: wrapped\n"
        "\tat org.junit.jupiter.api.Assertions.fail(Assertions.java:10)\n"
        "Caused by: java.io.IOException: refused\n"
        "\tat java.net.Socket.connect(Socket.java:1)\n"
        "\tat com.acme.pay.Client.call(Client.java:77)\n"
    )
    assert gh.locate_java_frame(trace, ROOTS) == (
        "payments/src/test/java/com/acme/pay/Client.java", 77,
    )


def test_the_longest_matching_prefix_wins_and_prefixes_are_dotted():
    roots = [
        {"prefix": "com.acme", "root": "core/src/test/java"},
        {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
    ]
    assert gh.locate_java_frame(_frame("com.acme.pay.A.b(A.java:1)"), roots)[0] == (
        "payments/src/test/java/com/acme/pay/A.java"
    )
    # "com.acme.payroll" is not inside "com.acme.pay": the dotted ancestor rule.
    assert gh.locate_java_frame(_frame("com.acme.payroll.A.b(A.java:1)"), roots)[0] == (
        "core/src/test/java/com/acme/payroll/A.java"
    )


def test_a_frame_no_root_places_is_skipped_and_the_next_is_tried():
    roots = [{"prefix": "com.acme.pay", "root": "payments/src/test/java"}]   # no "" root
    trace = (
        "java.lang.AssertionError\n"
        "\tat org.other.Helper.h(Helper.java:5)\n"
        "\tat com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)\n"
    )
    assert gh.locate_java_frame(trace, roots) == (
        "payments/src/test/java/com/acme/pay/CheckoutTest.java", 42,
    )
    assert gh.locate_java_frame(_frame("org.other.Helper.h(Helper.java:5)"), roots) is None


def test_vendor_and_non_positive_frames_never_locate():
    assert gh.locate_java_frame(_frame("org.junit.Assert.fail(Assert.java:89)"), ROOTS) is None
    assert gh.locate_java_frame(_frame("kotlin.collections.A.b(A.kt:3)"), ROOTS) is None
    assert gh.locate_java_frame(_frame("com.acme.pay.A.b(A.java:0)"), ROOTS) is None
    # "javafoo." is not "java.": the vendor test compares on the dotted name.
    assert gh.locate_java_frame(_frame("javafoo.A.b(A.java:2)"), ROOTS) == (
        "src/test/java/javafoo/A.java", 2,
    )


def test_a_dot_root_is_the_repository_root():
    roots = [{"prefix": "", "root": "."}]
    assert gh.locate_java_frame(_frame("com.acme.A.b(A.java:4)"), roots) == ("com/acme/A.java", 4)


# ── no roots = exactly today's answers ───────────────────────────────────────

_PY_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "tests/test_checkout.py", line 42, in test_pay\n'
    '  File "app/services/pay.py", line 88, in charge\n'
    "ValueError: boom\n"
)
_JS_TRACE = (
    "AssertionError: expected 200 to equal 500\n"
    "    at node_modules/chai/lib/assert.js:100:5\n"
    "    at Context.<anonymous> (cypress/e2e/checkout.cy.ts:23:9)\n"
)


@pytest.mark.parametrize("roots", [None, []])
def test_without_roots_every_answer_is_unchanged(roots):
    assert gh.locate_in_trace(_PY_TRACE, java_roots=roots) == ("app/services/pay.py", 88)
    assert gh.locate_in_trace(_JS_TRACE, java_roots=roots) == ("cypress/e2e/checkout.cy.ts", 23)
    assert gh.locate_in_trace(JUNIT_TRACE, java_roots=roots) is None
    assert gh.locate_in_trace(JUNIT_TRACE) is None
    assert cs.locate_failure_path(JUNIT_TRACE, None, java_roots=roots) is None
    assert cs.locate_failure_path(JUNIT_TRACE, None) is None


def test_python_and_js_still_win_over_java_when_roots_exist():
    assert gh.locate_in_trace(_PY_TRACE + JUNIT_TRACE, java_roots=ROOTS) == ("app/services/pay.py", 88)


def test_has_java_frame():
    assert gh.has_java_frame(JUNIT_TRACE)
    assert not gh.has_java_frame(_PY_TRACE)
    assert not gh.has_java_frame(None)


# ── validation (PUT / preview) and the read-side sanitiser ───────────────────

_ROOT_TEXT = (
    'Source root "{}" must be a relative path inside the repository '
    "(letters, digits, . _ - /; no ..)."
)


@pytest.mark.parametrize("root", ["../x", "a/../b", "/abs", "C:/x", "a\\b", "http://x", "", "   ", "a b", "src/test/java;rm"])
def test_a_root_outside_the_repository_is_refused(root):
    with pytest.raises(cs.SourceRootsError) as exc:
        cs.validate_source_roots([{"prefix": "", "root": root}])
    assert str(exc.value) == _ROOT_TEXT.format(root.strip())


@pytest.mark.parametrize("prefix", ["com..acme", "1com.acme", "com.acme.", ".com", "com-acme", "com.acme pay"])
def test_a_prefix_that_is_not_a_dotted_package_is_refused(prefix):
    with pytest.raises(cs.SourceRootsError) as exc:
        cs.validate_source_roots([{"prefix": prefix, "root": "src/test/java"}])
    assert str(exc.value) == (
        f'Package prefix "{prefix.strip()}" must be a dotted Java package, e.g. com.acme.pay.'
    )


def test_more_than_twenty_roots_is_refused():
    roots = [{"prefix": f"p{i}", "root": "src"} for i in range(21)]
    with pytest.raises(cs.SourceRootsError) as exc:
        cs.validate_source_roots(roots)
    assert str(exc.value) == "At most 20 source roots."
    assert len(cs.validate_source_roots(roots[:20])) == 20


def test_a_prefix_listed_twice_is_refused():
    with pytest.raises(cs.SourceRootsError) as exc:
        cs.validate_source_roots([
            {"prefix": "com.acme", "root": "a"}, {"prefix": " com.acme ", "root": "b"},
        ])
    assert str(exc.value) == 'Package prefix "com.acme" is listed twice.'


def test_roots_are_stored_stripped_without_a_trailing_slash_in_order():
    assert cs.validate_source_roots([
        {"prefix": " com.acme.pay ", "root": " payments/src/test/java/ "},
        {"prefix": "", "root": "src/test/java"},
    ]) == [
        {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
        {"prefix": "", "root": "src/test/java"},
    ]
    assert cs.validate_source_roots([]) == []


def test_the_read_side_drops_bad_entries_instead_of_raising():
    stored = [
        {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
        {"prefix": "", "root": "../../etc"},          # hand-edited: dropped
        "not an object",                               # dropped
        {"prefix": "com.acme.pay", "root": "dup"},     # duplicate prefix: dropped
        {"prefix": "", "root": "src/test/java/"},
    ]
    assert cs.sanitize_stored_source_roots(stored) == [
        {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
        {"prefix": "", "root": "src/test/java"},
    ]
    assert cs.sanitize_stored_source_roots(None) == []
    assert cs.sanitize_stored_source_roots({"prefix": "", "root": "x"}) == []


@pytest.mark.asyncio
async def test_load_source_roots_never_raises():
    pid = uuid.uuid4()
    ok = SimpleNamespace(scalar=AsyncMock(return_value=[{"prefix": "", "root": "src/test/java"}]))
    assert await cs.load_source_roots(ok, pid) == [{"prefix": "", "root": "src/test/java"}]
    null = SimpleNamespace(scalar=AsyncMock(return_value=None))
    assert await cs.load_source_roots(null, pid) == []
    broken = SimpleNamespace(scalar=AsyncMock(side_effect=RuntimeError("db down")))
    assert await cs.load_source_roots(broken, pid) == []
    assert await cs.load_source_roots(ok, None) == []


# ── threading: coverage, preview, inbox reason, assignment, annotations ─────


def _scalars(rows):
    res = MagicMock()
    res.scalars = MagicMock(return_value=SimpleNamespace(all=lambda: rows))
    return res


def _all(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _rule(pattern, owner, *, service="CODEOWNERS"):
    return SimpleNamespace(
        id=uuid.uuid4(), match_type="path", match_pattern=pattern, service_name=service,
        team_name=owner, team_contact=owner, priority=0, is_active=True, created_at=None,
    )


class _DB:
    """Scripted ``execute`` results in order, ``scalar`` = the project's stored
    roots; records every write so "writes nothing" is checkable."""

    def __init__(self, selects, roots=None):
        self._selects = list(selects)
        self.roots = roots
        self.scalar_calls = 0
        self.updates = []
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, stmt, *a, **k):
        if getattr(stmt, "is_update", False):
            self.updates.append(stmt)
            return MagicMock(rowcount=1)
        return self._selects.pop(0)

    async def scalar(self, stmt, *a, **k):
        self.scalar_calls += 1
        assert "source_roots" in str(stmt)
        return self.roots


def _java_row(name, frame):
    return SimpleNamespace(test_name=name, stack_trace=_frame(frame), error_message=None)


SAMPLE = [
    _java_row("pays", "com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)"),
    _java_row("refunds", "com.acme.pay.RefundTest.refunds(RefundTest.java:8)"),
    _java_row("other", "com.other.OtherTest.t(OtherTest.java:3)"),
    SimpleNamespace(test_name="py", stack_trace='File "web/x.py", line 3, in f', error_message=None),
]


@pytest.mark.asyncio
async def test_coverage_of_an_all_java_project_becomes_a_number_once_a_root_is_set():
    rules = [_rule("payments/**", "@alice")]
    java_only = SAMPLE[:3]

    before = await cs.compute_coverage(_DB([_scalars(rules), _all(java_only)], roots=None), uuid.uuid4())
    assert (before["located"], before["coverage_pct"], before["source_roots"]) == (0, None, 0)

    db = _DB([_scalars(rules), _all(java_only)], roots=ROOTS)
    after = await cs.compute_coverage(db, uuid.uuid4())
    assert after["sampled"] == 3
    assert after["located"] == 3       # two under payments/, one under src/test/java
    assert after["matched"] == 2
    assert after["coverage_pct"] == 66.7
    assert after["source_roots"] == 2
    assert db.scalar_calls == 1        # once per call, never per row


@pytest.mark.asyncio
async def test_preview_counts_what_the_given_roots_would_locate_and_writes_nothing():
    rules = [_rule("payments/**", "@alice")]
    db = _DB([_scalars(rules), _all(SAMPLE)], roots=None)   # stored: none
    preview = await cs.preview_source_roots(db, uuid.uuid4(), ROOTS)
    assert preview == {
        "sampled": 4,
        "java_frames": 3,
        "located": 3,
        "matched": 2,
        "lookback_days": 30,
        "examples": [
            {"test_name": "pays", "path": "payments/src/test/java/com/acme/pay/CheckoutTest.java"},
            {"test_name": "refunds", "path": "payments/src/test/java/com/acme/pay/RefundTest.java"},
            {"test_name": "other", "path": "src/test/java/com/other/OtherTest.java"},
        ],
    }
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    assert db.updates == [] and db.scalar_calls == 0   # the GIVEN roots, not the stored ones

    narrow = await cs.preview_source_roots(
        _DB([_scalars([]), _all(SAMPLE)]), uuid.uuid4(),
        [{"prefix": "com.acme.pay", "root": "payments/src/test/java"}],
    )
    assert (narrow["java_frames"], narrow["located"], narrow["matched"]) == (3, 2, 0)

    none = await cs.preview_source_roots(_DB([_scalars(rules), _all(SAMPLE[3:])]), uuid.uuid4(), ROOTS)
    assert (none["sampled"], none["java_frames"], none["located"], none["examples"]) == (1, 0, 0, [])


@pytest.mark.asyncio
async def test_the_inbox_reason_covers_a_java_failure_through_the_roots():
    pid, alice = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(), project_id=pid, assigned_to_user_id=alice,
        stack_trace=JUNIT_TRACE, error_message=None,
    )
    users = [SimpleNamespace(id=alice, username="alice", email="a@x.com")]

    with_roots = await cs.codeowners_reasons_for_rows(
        _DB([_scalars([_rule("payments/**", "@alice")]), _all(users)], roots=ROOTS), [row],
    )
    assert with_roots == {row.id: "via CODEOWNERS: payments/**"}

    without = await cs.codeowners_reasons_for_rows(
        _DB([_scalars([_rule("payments/**", "@alice")]), _all(users)], roots=None), [row],
    )
    assert without == {}


def _assign_selects(failures, *, pool, rules, users):
    return [
        _all(failures),
        SimpleNamespace(first=lambda: (None, None)),
        _all(pool),
        SimpleNamespace(scalar_one_or_none=lambda: None),   # run.primary_suite_name
        _all([]),                                          # TestSuiteOwner rows
        _scalars(rules),
        _all(users),
    ]


@pytest.mark.asyncio
async def test_ingest_assigns_a_java_failure_to_its_codeowners_member_with_a_root():
    from app.services.failed_test_assignment_service import assign_failed_tests_to_suite_owners

    alice, lead = uuid.uuid4(), uuid.uuid4()

    def _failures():
        return [SimpleNamespace(
            id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None,
            test_fingerprint="fp", stack_trace=JUNIT_TRACE, error_message=None,
        )]

    pool = [SimpleNamespace(user_id=lead, role=UserRole.QA_LEAD.value)]
    rules = [_rule("payments/**", "@alice")]
    users = [SimpleNamespace(id=alice, username="alice", email="a@x.com")]

    db = _DB(_assign_selects(_failures(), pool=pool, rules=rules, users=users), roots=ROOTS)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["path_owner"] == 1 and counts["assigned"] == 1
    assert len(db.updates) == 1
    assert db.updates[0].compile().params["assigned_to_user_id"] == alice

    # Without roots the same failure falls to the QA-lead pool, as today.
    db = _DB(_assign_selects(_failures(), pool=pool, rules=rules, users=users), roots=None)
    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["path_owner"] == 0 and counts["assigned"] == 1
    assert db.updates[0].compile().params["assigned_to_user_id"] == lead


def test_the_check_run_annotation_gets_the_path_and_line():
    tc = SimpleNamespace(
        id=uuid.uuid4(), test_fingerprint="fp", status="FAILED", test_name="pays",
        error_message="expected:<200> but was:<500>", stack_trace=JUNIT_TRACE,
    )
    with_roots = gh._CheckEnrichment(newly_failed=[("fp", tc)], has_baseline=True, java_roots=ROOTS)
    annotations, non_locatable, _ = gh._build_annotations(with_roots)
    assert non_locatable == []
    assert (annotations[0]["path"], annotations[0]["start_line"]) == (
        "payments/src/test/java/com/acme/pay/CheckoutTest.java", 42,
    )

    without = gh._CheckEnrichment(newly_failed=[("fp", tc)], has_baseline=True)
    annotations, non_locatable, _ = gh._build_annotations(without)
    assert annotations == [] and [r["name"] for r in non_locatable] == ["pays"]


@pytest.mark.asyncio
async def test_gather_enrichment_loads_the_roots_once_per_run():
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    tc = SimpleNamespace(id=uuid.uuid4(), test_fingerprint="fp", status="FAILED", test_name="t",
                         error_message=None, stack_trace=JUNIT_TRACE)
    db = _DB([], roots=ROOTS)
    with patch.object(gh, "_load_test_rows", AsyncMock(return_value={"fp": tc})), \
         patch("app.services.github_pr_comment_service._select_baseline", AsyncMock(return_value=None)), \
         patch("app.services.github_pr_comment_service._flaky_fingerprints", AsyncMock(return_value=set())), \
         patch("app.services.kind_evidence.kind_labels_for_test_cases", AsyncMock(return_value={})):
        enrich = await gh._gather_enrichment(db, run)
    assert enrich is not None
    assert enrich.java_roots == ROOTS
    assert db.scalar_calls == 1
