"""W36 STRAT-08 — the pure "Likely affected tests" ranker (``services/test_impact_service``).

Pins: the published weights and the co-failure saturation; ``None`` (not
measured) versus ``0`` (measured, none) for co-failure; the prefilter never
drops a file the exact ``path_overlap`` scores above 0 (a seeded randomized
comparison with brute force plus a table of each rule's edge); the caps (2,000
changed paths, 5 reason paths, ``limit``); the order (score desc, fingerprint
asc); and the flaky rule copied from ``failure_attribution_service.compose``.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from app.services import test_impact_service as tis
from app.services.commit_attribution_service import _FailingLocator, build_locator, path_overlap


def _tc(fp, *, name="test_case", cls=None, pkg=None, status="PASSED", trace=None):
    return SimpleNamespace(test_fingerprint=fp, test_name=name, class_name=cls, package_name=pkg,
                           status=status, stack_trace=trace, error_message=None)


CHARGE = _tc("fp-charge", name="test_charge_succeeds", cls="payments.ChargeTest", status="FAILED")  # stem 1.0
INVOICE = _tc("fp-invoice", name="test_totals", cls="acme.billing.Invoice")                       # segment 0.7
CART = _tc("fp-cart", name="test_cart", cls="cart.CartTest")                                       # no overlap
FILES = ["payments/charge.py", "web/billing/view.ts", "docs/README.md"]


def _by_fp(rows):
    return {row["test_fingerprint"]: row for row in rows}


# ── Weights, saturation, null vs 0 ────────────────────────────────────────────


def test_published_constants():
    assert (tis.W_OVERLAP, tis.W_COFAIL, tis.COFAIL_SATURATION) == (0.6, 0.4, 5)
    assert (tis.MAX_CHANGED_PATHS, tis.MAX_REASON_PATHS, tis.DEFAULT_LIMIT, tis.MAX_LIMIT) == (2000, 5, 20, 50)
    assert tis.ADVISORY_CAVEAT == (
        "Advisory only: ranked by path overlap and past co-failures. It never selects or skips tests.")


def test_with_history_the_score_is_the_weighted_sum():
    rows = _by_fp(tis.rank_affected_tests(
        FILES, [CHARGE, INVOICE, CART], {"fp-charge": 5, "fp-invoice": 2, "fp-cart": 1},
        history_available=True, history_runs_considered=9))
    assert rows["fp-charge"]["score"] == 1.0                              # 0.6·1.0 + 0.4·1
    assert rows["fp-invoice"]["score"] == round(0.6 * 0.7 + 0.4 * 0.4, 4)  # 0.58
    assert rows["fp-cart"]["score"] == round(0.4 * 0.2, 4)                 # co-failure only: 0.08
    assert rows["fp-cart"]["reason"]["overlapping_paths"] == [] and rows["fp-cart"]["reason"]["overlap_score"] == 0.0
    for row in rows.values():
        assert row["reason"]["history_runs_considered"] == 9
    assert rows["fp-invoice"]["reason"]["co_failure_runs"] == 2


@pytest.mark.parametrize("runs, expected", [(0, 0.6), (1, 0.68), (4, 0.92), (5, 1.0), (6, 1.0), (500, 1.0)])
def test_co_failure_saturates_at_five(runs, expected):
    (row,) = tis.rank_affected_tests(["payments/charge.py"], [CHARGE], {"fp-charge": runs},
                                     history_available=True, history_runs_considered=600)
    assert row["score"] == expected and row["reason"]["co_failure_runs"] == runs


def test_without_history_co_failure_is_null_not_zero_and_the_score_is_the_overlap():
    rows = _by_fp(tis.rank_affected_tests(
        FILES, [CHARGE, INVOICE, CART], {"fp-charge": 5, "fp-cart": 5},  # counts present but must be ignored
        history_available=False, history_runs_considered=7))
    assert set(rows) == {"fp-charge", "fp-invoice"}                         # cart has no overlap: not listed
    assert rows["fp-charge"]["score"] == 1.0 and rows["fp-invoice"]["score"] == 0.7
    for row in rows.values():
        assert row["reason"]["co_failure_runs"] is None
        assert row["reason"]["history_runs_considered"] is None


def test_with_history_an_absent_count_is_a_measured_zero():
    (row,) = tis.rank_affected_tests(["payments/charge.py"], [CHARGE], {}, history_available=True,
                                     history_runs_considered=0)
    assert row["reason"]["co_failure_runs"] == 0 and row["reason"]["history_runs_considered"] == 0
    assert row["score"] == 0.6


def test_only_a_positive_score_is_listed_and_the_row_shape_is_the_contract():
    rows = tis.rank_affected_tests(["docs/README.md"], [CHARGE, CART], {}, history_available=True,
                                   history_runs_considered=3)
    assert rows == []
    (row,) = tis.rank_affected_tests(["payments/charge.py"], [CHARGE], {}, history_available=False)
    assert row == {
        "test_fingerprint": "fp-charge", "test_name": "test_charge_succeeds", "class_name": "payments.ChargeTest",
        "status_in_run": "FAILED", "score": 1.0,
        "reason": {"overlapping_paths": ["payments/charge.py"], "overlap_score": 1.0, "co_failure_runs": None,
                   "history_runs_considered": None, "known_flaky": False},
    }


# ── The prefilter never drops a scoring file ──────────────────────────────────

_WORDS = ["payments", "charge", "refund", "cart", "auth", "a", "io", "db", "user", "users", "service", "api",
          "checkout", "order", "orders", "utils", "core", "src", "test", "tests", "lib", "main", "com", "acme",
          "gateway", "client", "x", "ab", "abc", "chargeback", "billing", "invoice", "pay", "__tests__", "e2e"]
_EXTS = [".py", ".java", ".ts", ".spec.ts", ".test.js", "", ".kt", ".tsx"]


def _random_path(rng):
    segs = [rng.choice(_WORDS) for _ in range(rng.randint(0, 4))]
    stem = rng.choice(_WORDS) + rng.choice(["", "_test", "Test", "", "_service", "s", "Spec"])
    if rng.random() < 0.2:
        stem = "test_" + stem
    return "/".join(segs + [stem + rng.choice(_EXTS)])


def _random_test(rng, i):
    cls = ".".join(rng.choice(_WORDS) for _ in range(rng.randint(0, 3))) or None
    if cls and rng.random() < 0.5:
        cls += "." + rng.choice(_WORDS).capitalize() + rng.choice(["Test", "Spec", ""])
    trace = None
    if rng.random() < 0.3:  # a located failure: dir sets + a located stem
        trace = f'Traceback (most recent call last):\n  File "{_random_path(rng)}", line 12, in t\nAssertionError'
    return _tc(f"fp{i:04d}", name=f"{rng.choice(_WORDS)}_{rng.choice(_WORDS)}[{i}]", cls=cls,
               pkg=rng.choice([None, "com.acme." + rng.choice(_WORDS)]), trace=trace)


def _without(loc, drop):
    """The literal locator the B7 rule describes: ``loc`` minus the dropped module tokens (stems, dirs kept)."""
    out = _FailingLocator()
    out.located_paths, out.dir_sets, out.stems = set(loc.located_paths), [set(d) for d in loc.dir_sets], set(loc.stems)
    out.module_tokens = set(loc.module_tokens) - set(drop)
    return out


def _brute(loc, files, drop=frozenset()):
    bare = _without(loc, drop)
    return {(path_overlap(bare, f), f) for f in files if path_overlap(bare, f) > 0}


def test_prefilter_equals_brute_force_on_random_realistic_paths():
    rng = random.Random(20261106)
    compared = scoring = filtered_trials = 0
    for _trial in range(150):
        files = list(dict.fromkeys(_random_path(rng) for _ in range(rng.randint(1, 40))))
        index = tis._ChangedFileIndex(files)
        locs = [build_locator([_random_test(rng, i)]) for i in range(25)]
        drop = tis.uninformative_tokens(locs)  # the run's filter, as the ranker applies it
        filtered_trials += bool(drop)
        for loc in locs:
            for applied in (drop, frozenset()):  # with the filter, and the raw path_overlap parity
                best, hits = tis.overlap_for(index, loc, applied)
                exact = _brute(loc, files, applied)
                assert set(hits) == exact, (loc.stems, loc.module_tokens, applied, sorted(exact - set(hits))[:3])
                assert best == max((v for v, _f in exact), default=0.0)
                compared += 1
                scoring += bool(exact)
    # Not vacuous: thousands of scoring comparisons, and the filter really dropped tokens in many trials.
    assert compared == 7500 and scoring > 2000 and filtered_trials > 20


@pytest.mark.parametrize("cls, trace_path, changed, rule", [
    ("payments.ChargeTest", None, "src/payments/charge.py", "A stem"),
    ("acme.billing.Invoice", None, "web/billing/view.ts", "B module token as a segment"),
    (None, "tests/payments/test_charge.py", "payments/api/handler.py", "C located directory"),
    ("x.chargeback", None, "lib/charge.py", "D stem inside a token"),
    ("x.pay", None, "lib/payments_gateway.py", "D token inside a stem"),
    ("a.B", None, "src/abc.py", "D one-letter token"),
    ("q.averyveryverylongclassnamewithchargeinsideofitthatgoesonandonandon", None, "z/charge.py",
     "D long token (stem scan branch)"),
    ("odd.we\x00ird", None, "x/we\x00irdo.py", "D token with a NUL (direct branch)"),
])
def test_each_overlap_rule_survives_the_prefilter(cls, trace_path, changed, rule):
    trace = f'Traceback:\n  File "{trace_path}", line 3, in t\nAssertionError' if trace_path else None
    loc = build_locator([_tc("fp", name="zz_unrelated", cls=cls, trace=trace)])
    noise = ["docs/README.md", "ops/k8s/deploy.yaml", "frontend/src/App.tsx"]
    files = noise + [changed]
    assert path_overlap(loc, changed) > 0, rule
    best, hits = tis.overlap_for(tis._ChangedFileIndex(files), loc)
    assert set(hits) == _brute(loc, files) and best == path_overlap(loc, changed), rule


def test_a_segment_less_path_and_an_empty_range_score_nothing():
    loc = build_locator([CHARGE])
    assert tis.overlap_for(tis._ChangedFileIndex(["", "./", "."]), loc) == (0.0, [])
    assert tis.overlap_for(tis._ChangedFileIndex([]), loc) == (0.0, [])


# ── Caps and order ────────────────────────────────────────────────────────────


def test_only_the_first_2000_distinct_changed_paths_are_scored():
    filler = [f"vendor/blob{i:05d}.bin" for i in range(tis.MAX_CHANGED_PATHS - 1)]
    duplicated = filler + filler[:300]                      # duplicates do not use up the cap
    at_cap = tis.rank_affected_tests(duplicated + ["payments/charge.py"], [CHARGE], {}, history_available=False)
    assert [row["test_fingerprint"] for row in at_cap] == ["fp-charge"]  # path #2000 is scored
    past_cap = tis.rank_affected_tests(filler + ["ops/x.yaml", "payments/charge.py"], [CHARGE], {},
                                       history_available=False)
    assert past_cap == []                                   # path #2001 is not


def test_at_most_five_reason_paths_strongest_first():
    files = ["payments/zeta.py", "payments/alpha.py", "web/charge.ts", "payments/beta.py", "payments/gamma.py",
             "payments/delta.py", "payments/eps.py"]
    (row,) = tis.rank_affected_tests(files, [CHARGE], {}, history_available=False)
    assert row["reason"]["overlapping_paths"] == [
        "web/charge.ts",                                    # stem 1.0 first
        "payments/alpha.py", "payments/beta.py", "payments/delta.py", "payments/eps.py",  # 0.7, by path
    ]
    assert row["reason"]["overlap_score"] == 1.0


def test_order_is_score_desc_then_fingerprint_and_limit_cuts():
    # Four equal 0.7 scores, each through its OWN module token (one shared by 4 of 5 tests would be dropped, B7).
    words = {"d": "billing", "c": "orders", "a": "users", "b": "search"}
    tests = [_tc(f"fp-{c}", name="t", cls=f"{words[c]}.Thing") for c in "dcab"]
    tests.append(_tc("fp-z", name="t", cls="payments.ChargeTest"))
    files = ["payments/charge.py"] + [f"web/{w}/view.ts" for w in words.values()]
    rows = tis.rank_affected_tests(files, tests, {}, history_available=False, limit=20)
    assert [r["test_fingerprint"] for r in rows] == ["fp-z", "fp-a", "fp-b", "fp-c", "fp-d"]
    assert [r["test_fingerprint"] for r in tis.rank_affected_tests(files, tests, {}, history_available=False,
                                                                   limit=3)] == ["fp-z", "fp-a", "fp-b"]
    assert tis.rank_affected_tests(files, list(reversed(tests)), {}, history_available=False) == rows


# ── B7: a module token most of the run's tests carry does not count ──────────

JAVA_FILES = [
    "src/main/java/com/acme/payments/ChargeService.java",
    "src/main/java/com/acme/payments/RefundPolicy.java",
    "src/main/java/com/acme/catalog/Product.java",
]
JAVA_TESTS = [  # the org token ``acme`` is in every locator and every changed path
    _tc("fp-1", name="shouldChargeCard", cls="com.acme.payments.ChargeServiceTest"),  # stem ChargeService
    _tc("fp-2", name="shouldTotalInvoice", cls="com.acme.payments.InvoiceTest"),      # module token payments
    _tc("fp-3", name="shouldLogIn", cls="com.acme.users.LoginTest"),                  # acme only
    _tc("fp-4", name="shouldFindItems", cls="com.acme.search.QueryTest"),             # acme only
    _tc("fp-5", name="shouldSignUp", cls="com.acme.users.SignupTest"),                # acme only
    _tc("fp-6", name="shouldListProduct", cls="com.acme.catalog.ProductTest"),        # stem Product
]


def test_published_token_rule_constants():
    assert (tis.UNINFORMATIVE_TOKEN_SHARE, tis.MIN_TESTS_FOR_TOKEN_FILTER) == (0.5, 4)


def test_uninformative_tokens_is_at_least_half_of_four_or_more_tests():
    locs = [build_locator([_tc(f"fp{i}", name="t", cls=cls)]) for i, cls in enumerate(
        ["acme.payments.A", "acme.payments.B", "acme.users.C", "acme.search.D"])]
    assert tis.uninformative_tokens(locs) == {"acme", "payments"}   # 4 of 4 and 2 of 4 (exactly half)
    assert tis.uninformative_tokens(locs[:3]) == frozenset()        # 3 tests: the rule is not applied


def test_the_org_token_in_every_java_path_no_longer_lists_every_test():
    index = tis._ChangedFileIndex(JAVA_FILES)
    before = {tc.test_fingerprint: tis.overlap_for(index, build_locator([tc]))[0] for tc in JAVA_TESTS}
    assert all(score >= 0.7 for score in before.values())          # without the rule: everyone >= 0.7
    assert tis.uninformative_tokens([build_locator([tc]) for tc in JAVA_TESTS]) == {"acme"}  # users: 2 of 6
    rows = tis.rank_affected_tests(JAVA_FILES, JAVA_TESTS, {}, history_available=False)
    assert [(r["test_fingerprint"], r["score"], r["reason"]["overlapping_paths"]) for r in rows] == [
        ("fp-1", 1.0, ["src/main/java/com/acme/payments/ChargeService.java",
                       "src/main/java/com/acme/payments/RefundPolicy.java"]),   # stem 1.0, payments 0.7
        ("fp-6", 1.0, ["src/main/java/com/acme/catalog/Product.java"]),          # stem 1.0
        ("fp-2", 0.7, ["src/main/java/com/acme/payments/ChargeService.java",
                       "src/main/java/com/acme/payments/RefundPolicy.java"]),   # module-specific token
    ]


def test_a_run_of_three_tests_keeps_every_token():
    small = [JAVA_TESTS[0], JAVA_TESTS[2], JAVA_TESTS[3]]           # acme in 3 of 3, but only 3 tests
    rows = tis.rank_affected_tests(JAVA_FILES, small, {}, history_available=False)
    assert [(r["test_fingerprint"], r["score"]) for r in rows] == [("fp-1", 1.0), ("fp-3", 0.7), ("fp-4", 0.7)]


def test_stems_and_dir_sets_survive_the_token_filter():
    # Every test fails in payments/: the dir token is dropped from rule B, but rule C (dir set) and the stems stay.
    trace = 'Traceback:\n  File "payments/test_{0}.py", line 3, in t\nAssertionError'
    tests = [_tc(f"fp-{s}", name="t", trace=trace.format(s)) for s in ("charge", "refund", "cart", "auth")]
    assert tis.uninformative_tokens([build_locator([tc]) for tc in tests]) == {"payments"}
    rows = {r["test_fingerprint"]: r for r in tis.rank_affected_tests(
        ["payments/charge.py", "payments/api/handler.py"], tests, {}, history_available=False)}
    assert rows["fp-charge"]["score"] == 1.0                        # stem
    for fp in ("fp-refund", "fp-cart", "fp-auth"):
        assert rows[fp]["score"] == 0.8                             # dir set {payments}: 1/1 · 0.8 (rule C)


# ── The flaky rule (failure_attribution_service.compose) ─────────────────────


@pytest.mark.parametrize("score, confidence, expected", [
    (0.5, "medium", True), (0.9, "high", True), (0.49, "high", False), (0.9, "low", False),
    (0.9, "none", False), (0.9, None, False), (None, "high", False), (float("nan"), "high", False),
    (7.0, "medium", True),                                  # clamped to 1.0, still >= the floor
])
def test_is_known_flaky_is_the_precedent_rule(score, confidence, expected):
    assert tis.is_known_flaky(score, confidence) is expected


def test_a_known_flaky_test_keeps_its_overlap_but_earns_no_co_failure_credit():
    rows = _by_fp(tis.rank_affected_tests(
        FILES, [CHARGE, CART], {"fp-charge": 5, "fp-cart": 5}, history_available=True,
        history_runs_considered=8, known_flaky={"fp-charge", "fp-cart"}))
    charge = rows["fp-charge"]
    assert charge["score"] == 0.6                           # 0.6·1.0 + 0 (not 1.0)
    assert charge["reason"]["co_failure_runs"] == 5         # the measured count is still reported
    assert charge["reason"]["known_flaky"] is True
    assert "fp-cart" not in rows                            # co-failure was its only evidence
    plain = _by_fp(tis.rank_affected_tests(FILES, [CHARGE, CART], {"fp-charge": 5, "fp-cart": 5},
                                           history_available=True, history_runs_considered=8))
    assert plain["fp-charge"]["score"] == 1.0 and plain["fp-cart"]["score"] == 0.4


def test_without_history_known_flaky_is_a_label_only():
    (row,) = tis.rank_affected_tests(["payments/charge.py"], [CHARGE], {}, history_available=False,
                                     known_flaky={"fp-charge"})
    assert row["score"] == 1.0 and row["reason"]["known_flaky"] is True


# ── Range helpers ─────────────────────────────────────────────────────────────


def test_changed_paths_dedupes_in_commit_order_and_ignores_junk():
    commits = [{"files": ["b.py", "a.py"]}, "junk", {"files": None}, {"files": ["a.py", "", 7, "c.py"]}]
    assert tis.changed_paths(commits) == ["b.py", "a.py", "c.py"]
    assert tis.changed_paths(None) == []


def test_file_detail_coverage_is_null_without_commits():
    assert tis.file_detail_coverage([]) is None and tis.file_detail_coverage(None) is None
    assert tis.file_detail_coverage([{"files": []}, {"files": []}]) == 0.0
    assert tis.file_detail_coverage([{"files": ["a"]}, {"files": []}, {"files": ["b"]}]) == 0.6667
