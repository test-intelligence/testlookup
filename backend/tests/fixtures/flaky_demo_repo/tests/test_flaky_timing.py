"""A deliberately time-flaky test for the Fixer sandbox validation demo.

Runnable as `pytest tests/test_flaky_timing.py` OR `python tests/...` (exits
non-zero on failure), so the sandbox works in a bare python image with no
pytest and no network. As shipped it fails ~50% of the time; the Fixer's
test-code-only patch stabilises the assertion.
"""
import time


def test_flaky_timing():
    # FLAKY: sub-millisecond wall-clock parity, non-deterministic.
    assert int(time.time() * 1000) % 2 == 0, "flaky timing failure"


if __name__ == "__main__":
    test_flaky_timing()
    print("ok")
