"""Regression — compliance packs are tamper-EVIDENT, never "signed".

US-13.3 compliance-audit finding 4: ``compliance_pack_service`` carried a
``# ZIP assembly + signing`` section header while the implementation is a
plain SHA-256 checksum chain — ``manifest.json`` digests every file in the
ZIP and ``compliance_packs.manifest_sha256`` digests the manifest. There is no
HMAC and no PKI anywhere in the path, so an actor able to rewrite BOTH the
MinIO object and the DB row forges a self-consistent pack.

The fix was wording, not crypto (signing is a product decision). These tests
keep the wording honest in both directions: no "signed/signature" claim may
creep back into the code or the pack's own README, and the residual risk must
stay documented where the guarantee is described.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import compliance_pack_service as svc  # noqa: E402

# "signed" / "signing" / "signature" NOT preceded by "not ", "un", "no " …
_CLAIMS_SIGNING = re.compile(r"\bsign(?:ed|ing|ature)\b", re.IGNORECASE)


def _readme_text() -> str:
    release = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        name="Release 9.9",
        version="9.9.0",
    )
    run = SimpleNamespace(id="33333333-3333-3333-3333-333333333333", build_number="b-1")
    decision = SimpleNamespace(
        recommendation="GO", risk_score=12, original_recommendation=None,
    )
    return svc._build_readme(
        release, decision, run, datetime.now(timezone.utc),
    ).decode("utf-8")


def test_module_docstring_labels_the_chain_tamper_evident_not_signed():
    doc = (svc.__doc__ or "").lower()
    assert "tamper-evident" in doc
    assert "no hmac and no pki" in doc
    # The residual risk is named, not glossed over.
    assert "rewrite both" in doc or "rewrite BOTH".lower() in doc
    assert "worm" in doc or "object-lock" in doc


def test_pack_readme_does_not_claim_a_signature():
    text = _readme_text()
    for match in _CLAIMS_SIGNING.finditer(text):
        window = text[max(0, match.start() - 30):match.start()].lower()
        assert "not" in window or "no " in window, (
            "compliance-pack README claims a signature it does not have: "
            f"...{text[max(0, match.start() - 60):match.end() + 40]}..."
        )


def test_pack_readme_states_what_the_chain_does_not_prove():
    text = _readme_text().lower()
    assert "not signed" in text
    assert "tamper-evident checksum chain" in text
    # Operator-side mitigation is offered rather than implied to be built in.
    assert "key custody" in text or "object-locked" in text


def test_service_has_no_signing_implementation_to_match_the_label():
    """If real signing ever lands, this test is the reminder to relabel."""
    source_names = dir(svc)
    assert not any("sign" in name.lower() for name in source_names), (
        "a signing helper appeared — update the docstrings/README wording that "
        "currently states the pack is NOT signed"
    )
    assert not hasattr(svc, "hmac")


def _migration_0114_text() -> str:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0114_compliance_pack_flag_description.py"
    )
    return path.read_text(encoding="utf-8")


def test_migration_0114_corrects_the_user_visible_flag_description():
    """0066 seeded the flag as 'signed'; 0114 fixes DEPLOYED instances too.

    Editing 0066's source would only reach fresh installs, so the correction
    has to be a data migration.
    """
    text = _migration_0114_text()
    assert 'revision = "0114"' in text
    assert 'down_revision = "0113"' in text
    # Replacement wording is the honest one.
    assert "tamper-evident" in text
    # Guarded so an operator who already reworded it is not clobbered.
    assert "LIKE '%signed%'" in text
    # Real downgrade, not a stub.
    downgrade_src = text.split("def downgrade()")[1]
    assert "UPDATE feature_flags" in downgrade_src
    assert "pass" not in downgrade_src.split("\n")[1:3]
