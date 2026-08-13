from app.services.evidence_sanitizer import sanitize_persistence_payload


def test_header_named_text_fields_are_fully_redacted():
    payload, stats = sanitize_persistence_payload({
        "raw_headers": "Authorization: Bearer sk-secret-value",
        "nested": {"headers": "Cookie: sid=secret-session"},
        "safe_summary": "password=hunter2",
    })

    assert payload["raw_headers"] == "[REDACTED]"
    assert payload["nested"]["headers"] == "[REDACTED]"
    assert "hunter2" not in payload["safe_summary"]
    assert stats.redacted_strings >= 3
