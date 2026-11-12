def test_report_broken():
    raise RuntimeError("intentional producer exception")
