"""Unit tests for search_ranking.py — pure functions, no DB required."""
from app.services.search_ranking import compute_hybrid_score, build_match_reasons


class TestComputeHybridScore:
    def test_keyword_match_only(self):
        score = compute_hybrid_score(True, 0.0, None, 0, "PASSED")
        assert 0.29 <= score <= 0.31  # 0.3 * 1.0

    def test_semantic_only(self):
        score = compute_hybrid_score(False, 0.8, None, 0, "PASSED")
        assert 0.23 <= score <= 0.25  # 0.3 * 0.8

    def test_full_signals(self):
        score = compute_hybrid_score(True, 0.9, None, 10, "FAILED")
        # 0.3*1 + 0.3*0.9 + 0.2*0 + 0.1*1 + 0.1*1 = 0.3 + 0.27 + 0 + 0.1 + 0.1 = 0.77
        assert 0.76 <= score <= 0.78

    def test_recurrence_capped_at_10(self):
        s1 = compute_hybrid_score(False, 0.0, None, 10, "PASSED")
        s2 = compute_hybrid_score(False, 0.0, None, 100, "PASSED")
        assert s1 == s2  # both capped at 1.0

    def test_status_broken_half_weight(self):
        failed = compute_hybrid_score(False, 0.0, None, 0, "FAILED")
        broken = compute_hybrid_score(False, 0.0, None, 0, "BROKEN")
        assert failed > broken

    def test_negative_failure_count_clamped(self):
        score = compute_hybrid_score(False, 0.0, None, -5, "PASSED")
        assert score >= 0.0

    def test_semantic_clamped_to_range(self):
        score = compute_hybrid_score(False, 1.5, None, 0, "PASSED")
        assert score <= 0.30 + 0.01  # max semantic contribution is 0.3

    def test_returns_float(self):
        result = compute_hybrid_score(False, 0.0, None, 0, "PASSED")
        assert isinstance(result, float)


class TestBuildMatchReasons:
    def test_keyword_match(self):
        reasons = build_match_reasons(True, 0.0, 0, "PASSED")
        assert any("Keyword" in r for r in reasons)

    def test_semantic_match(self):
        reasons = build_match_reasons(False, 0.85, 0, "PASSED")
        assert any("Semantic" in r for r in reasons)

    def test_recurring_failure(self):
        reasons = build_match_reasons(False, 0.0, 7, "PASSED")
        assert any("Recurring" in r for r in reasons)

    def test_currently_failing(self):
        reasons = build_match_reasons(False, 0.0, 0, "FAILED")
        assert any("failing" in r.lower() for r in reasons)

    def test_no_signals_uses_mode(self):
        reasons = build_match_reasons(False, 0.0, 0, "PASSED", "hybrid")
        assert any("hybrid" in r for r in reasons)

    def test_all_signals(self):
        reasons = build_match_reasons(True, 0.9, 5, "FAILED")
        assert len(reasons) >= 3
