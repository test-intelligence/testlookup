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


_MIGRATION_0115 = "0115_compliance_pack_flag_drop_signed_wording.py"


def _versions_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _migration_0114_text() -> str:
    return (
        _versions_dir() / "0114_compliance_pack_flag_description.py"
    ).read_text(encoding="utf-8")


def _folded_string_constants(path) -> list[str]:
    """Every string literal in a migration, with adjacent literals folded.

    Both migrations build their SQL out of implicitly-concatenated string
    literals, which Python folds at parse time — so the AST hands back one
    constant per statement and the tests can assert on the real value rather
    than on how it happens to be line-wrapped.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _seeded_description_0066() -> str:
    """The description literal 0066 INSERTs, pulled out of its SQL."""
    path = _versions_dir() / "0066_compliance_packs.py"
    sql = next(
        s
        for s in _folded_string_constants(path)
        if "INSERT INTO feature_flags" in s and "release_compliance_pack" in s
    )
    match = re.search(r"'release_compliance_pack',\s*'(.+?)',\s*false", sql)
    assert match, f"could not locate the seeded description in 0066: {sql}"
    return match.group(1)


def _migration_constant(filename: str, name: str) -> str:
    """A module-level ``NAME = "…"`` string constant from a migration."""
    import ast

    path = _versions_dir() / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
            and isinstance(node.value, ast.Constant)
        ):
            return node.value.value
    raise AssertionError(f"{filename} no longer defines {name}")


def _replacement_description_0114() -> str:
    """0114's ``_NEW`` — the interim wording, superseded by 0115."""
    return _migration_constant("0114_compliance_pack_flag_description.py", "_NEW")


def _final_description_0115() -> str:
    """0115's ``_FINAL`` — what every install ends up displaying."""
    return _migration_constant(_MIGRATION_0115, "_FINAL")


def _assert_no_unqualified_signing_claim(text: str, where: str) -> None:
    for match in _CLAIMS_SIGNING.finditer(text):
        window = text[max(0, match.start() - 30):match.start()].lower()
        assert "not" in window or "no " in window, (
            f"{where} claims a signature it does not have: "
            f"...{text[max(0, match.start() - 60):match.end() + 40]}..."
        )


def _assert_says_nothing_about_signing(text: str, where: str) -> None:
    """Stricter than the negation-window check: the words must not appear.

    A negated claim ("not cryptographically signed") is honest but still puts
    the word in front of a reader skimming Settings → Feature Flags, and it
    leaves "does this claim signing?" answerable only by parsing the sentence.
    Surfaces that name the mechanism instead ("SHA-256 checksum chain (no HMAC,
    no PKI)") need no negation, so they can be held to the flat rule.
    """
    match = _CLAIMS_SIGNING.search(text)
    if match is None:
        return
    raise AssertionError(
        f"{where} mentions signing at all — say what the integrity mechanism IS "
        f"(SHA-256 checksum chain, no HMAC, no PKI) rather than what it is not: "
        f"...{text[max(0, match.start() - 60):match.end() + 40]}..."
    )


def test_migration_0066_seeds_an_honest_flag_description():
    """Fresh installs must not be told the packs are signed.

    0066 seeded "Generate **signed** ZIP compliance packs…", which is visible
    verbatim in Settings → Feature Flags. 0114/0115 repair deployed rows; this
    guards the value a brand-new database starts from.
    """
    description = _seeded_description_0066()
    assert "tamper-evident" in description.lower()
    assert "sha-256" in description.lower()
    _assert_says_nothing_about_signing(description, "0066's seeded flag description")


def test_final_flag_description_says_nothing_about_signing():
    _assert_says_nothing_about_signing(
        _final_description_0115(), "0115's final flag description"
    )


def test_seeded_and_final_flag_descriptions_are_identical():
    """Fresh installs and upgraded installs must converge on one string.

    0115's guard is an exact match on the two known prior values, so a fresh
    database — seeded straight to the final wording by 0066 — is left alone by
    both data migrations. That is only correct while 0066's literal and 0115's
    ``_FINAL`` agree; if they drift, the same flag reads differently depending
    on how old the deployment is, with nothing to reconcile them.
    """
    assert _seeded_description_0066() == _final_description_0115()


def test_0115_recognises_every_earlier_wording_it_must_replace():
    """The exact-match guard has to know both prior values, or rows are stranded.

    Pre-0114 installs arrive at 0115 holding 0066's original text (0114 fixed
    them first, but a database restored from an old dump can arrive either way);
    at-0114 installs hold 0114's ``_NEW``. Miss one and that population keeps
    the stale description forever, silently.
    """
    original = _migration_constant(_MIGRATION_0115, "_ORIGINAL_0066")
    interim = _migration_constant(_MIGRATION_0115, "_INTERIM_0114")

    assert original == _migration_constant(
        "0114_compliance_pack_flag_description.py", "_OLD"
    ), (
        "0115's _ORIGINAL_0066 has drifted from 0066's pre-fix text (0114 kept a "
        "copy as _OLD) — pre-0114 rows would no longer match the guard"
    )
    assert interim == _replacement_description_0114(), (
        "0115's _INTERIM_0114 has drifted from 0114's actual _NEW — at-0114 "
        "installs would no longer match the guard and would keep the old text"
    )

    text = (_versions_dir() / _MIGRATION_0115).read_text(encoding="utf-8")
    upgrade_src = text.split("def upgrade()")[1].split("def downgrade()")[0]
    assert "IN (:original, :interim)" in upgrade_src, (
        "0115 must match the known prior values exactly, not LIKE '%signed%' — "
        "an operator-reworded description must never be clobbered"
    )


def test_migration_0115_is_a_real_migration_on_top_of_0114():
    text = (_versions_dir() / _MIGRATION_0115).read_text(encoding="utf-8")
    assert 'revision = "0115"' in text
    assert 'down_revision = "0114"' in text
    downgrade_src = text.split("def downgrade()")[1]
    assert "UPDATE feature_flags" in downgrade_src
    assert "_INTERIM_0114" in downgrade_src or "interim=" in downgrade_src


def test_interim_0114_wording_was_at_least_honest():
    """0114 stays as shipped — it is applied in the wild and must not be edited.

    It is held to the weaker rule (a negated claim is fine), which is exactly
    why 0115 exists.
    """
    _assert_no_unqualified_signing_claim(
        _replacement_description_0114(), "0114's interim flag description"
    )


def test_mcp_generate_compliance_pack_tool_makes_no_signing_claim():
    """The MCP tool docstring is the description an agent reasons over.

    ``generate_compliance_pack`` advertised "Produces a **signed** ZIP", so an
    agent asked whether a pack is cryptographically signed would have answered
    yes. Read from source rather than imported — the MCP package pulls in the
    server runtime, which these unit tests do not stand up.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3] / "mcp" / "tools" / "compliance_pack.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc = next(
        (
            ast.get_docstring(node) or ""
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == "generate_compliance_pack"
        ),
        None,
    )
    assert doc is not None, "generate_compliance_pack tool disappeared from the MCP surface"
    _assert_says_nothing_about_signing(doc, "the generate_compliance_pack MCP tool doc")
    assert "tamper-evident" in doc.lower()


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
