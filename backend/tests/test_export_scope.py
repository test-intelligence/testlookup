"""S6a — generalising the archive from a Release to an arbitrary scope.

The compliance-pack service already produces exactly what an export needs: a
ZIP whose ``manifest.json`` carries a SHA-256 of every other file, plus a
human-readable verification section. What it does not have is a way to describe
*what the archive covers* other than "a release".

Two properties are load-bearing here, and both are about not breaking evidence
that already exists:

1. **The release path must produce byte-identical output.** A generalisation
   that changes the manifest bytes makes every regenerated pack disagree with
   the digest an auditor recorded.
2. **One builder, not two.** The epic's warning is explicit: a half-generalised
   service with two entry points is worse than either.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.services import compliance_pack_service as svc  # noqa: E402


# ── the scope model ─────────────────────────────────────────────────────────


def test_a_release_scope_and_a_run_scope_are_both_expressible():
    """The whole point of the generalisation."""
    project_id, release_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    release_scope = svc.ExportScope.for_release(
        release_id=release_id, project_id=project_id, run_ids=[run_id]
    )
    run_scope = svc.ExportScope.for_runs(
        project_id=project_id, run_ids=[run_id], tier="summary"
    )

    assert release_scope.kind == "release"
    assert run_scope.kind == "runs"
    assert run_scope.project_id == project_id


def test_the_release_scope_defaults_to_the_full_tier():
    """A compliance pack is evidence and has always contained everything.

    Defaulting it to ``summary`` would silently thin an artifact auditors
    already rely on.
    """
    scope = svc.ExportScope.for_release(
        release_id=uuid.uuid4(), project_id=uuid.uuid4(), run_ids=[uuid.uuid4()]
    )
    assert scope.tier == "full"


def test_an_unknown_tier_is_refused():
    """A tier outside the vocabulary would select no content and produce an
    archive that looks successful and holds nothing."""
    with pytest.raises(ValueError, match="tier"):
        svc.ExportScope.for_runs(
            project_id=uuid.uuid4(), run_ids=[uuid.uuid4()], tier="everything"
        )


def test_an_empty_run_set_is_refused():
    """Exporting nothing, then deleting on the strength of it, is the worst
    outcome this slice can produce."""
    with pytest.raises(ValueError, match="at least one run"):
        svc.ExportScope.for_runs(project_id=uuid.uuid4(), run_ids=[], tier="summary")


# ── the size cap that did not exist ─────────────────────────────────────────


def test_an_oversized_candidate_set_is_refused_before_any_work():
    """No cap existed on compliance packs.

    An unbounded export of a large candidate set OOMs or times out — and it
    does so AFTER the operator was told the export would protect their data.
    Refusing up front with a stated bound is the honest failure.
    """
    too_many = [uuid.uuid4() for _ in range(svc.MAX_EXPORT_RUNS + 1)]

    with pytest.raises(ValueError) as exc:
        svc.ExportScope.for_runs(
            project_id=uuid.uuid4(), run_ids=too_many, tier="summary"
        )

    assert str(svc.MAX_EXPORT_RUNS) in str(exc.value), (
        "the refusal must state the bound, or the operator cannot act on it"
    )


def test_the_cap_is_a_stated_number_not_a_silent_truncation():
    """Truncating would export a subset and then delete the whole set."""
    import inspect

    source = inspect.getsource(svc.ExportScope)
    assert "[:MAX_EXPORT_RUNS]" not in source and "[: MAX_EXPORT_RUNS]" not in source


# ── the storage key ─────────────────────────────────────────────────────────


def test_the_release_key_is_unchanged():
    """Existing packs are addressed by this key. A changed shape orphans them.

    Date-prefixed so MinIO lifecycle rules can expire by year/month without
    listing the whole bucket — that property has to survive too.
    """
    release_id, pack_id = uuid.uuid4(), uuid.uuid4()
    at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    scope = svc.ExportScope.for_release(
        release_id=release_id, project_id=uuid.uuid4(), run_ids=[uuid.uuid4()]
    )
    key = svc.build_export_key(scope, at, pack_id)

    assert key == f"compliance/2026/06/01/{release_id}/{pack_id}.zip"


def test_a_run_scoped_key_is_distinguishable_from_a_release_one():
    """Two archives of different kinds must not be able to collide, and an
    operator reading the bucket should be able to tell them apart."""
    project_id, pack_id = uuid.uuid4(), uuid.uuid4()
    at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    scope = svc.ExportScope.for_runs(
        project_id=project_id, run_ids=[uuid.uuid4()], tier="summary"
    )
    key = svc.build_export_key(scope, at, pack_id)

    assert key.startswith("exports/2026/06/01/")
    assert str(project_id) in key
    assert key.endswith(f"{pack_id}.zip")
    assert not key.startswith("compliance/"), (
        "a run export under the compliance prefix would be picked up by "
        "lifecycle rules written for compliance packs"
    )


# ── one builder, not two ────────────────────────────────────────────────────


def test_the_release_path_delegates_to_the_scope_builder():
    """The epic's explicit warning: do not leave a half-generalised service
    with two entry points. A second copy of the manifest chain is a second
    thing that can drift from the verification instructions in the README."""
    import inspect

    source = inspect.getsource(svc.generate_pack)
    # Assert the CALL, not the name. Grepping for "ExportScope" passed against
    # a mutant that built the storage key inline, because the scope object was
    # still constructed on the line above — the same name-presence trap that
    # let mutants through twice already in this codebase.
    assert "build_export_key(" in source, (
        "generate_pack builds its own storage key instead of going through "
        "the shared builder"
    )
    assert 'f"compliance/' not in source, (
        "generate_pack hardcodes the compliance key shape; that is the second "
        "copy the epic warns about"
    )


def test_only_one_function_builds_a_manifest():
    """Two manifest builders is how the digest and the README's verification
    steps drift apart."""
    import inspect

    source = inspect.getsource(svc)
    assert source.count("def _build_manifest") == 1


# ── the release manifest must not move a byte ───────────────────────────────


def _manifest(scope, run=None, decision=None):
    import json

    at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    return json.loads(
        svc._build_manifest({"a.json": b"a"}, at, scope, run, decision)
    )


def test_the_release_manifest_emits_exactly_the_keys_it_always_has():
    """Pinned as a literal, because `_serialize` uses `sort_keys=True`.

    Any added key changes every byte of the manifest and therefore its digest.
    An auditor who recorded a pack's `manifest_sha256` and later regenerates
    the pack would get a different value and conclude the evidence had been
    tampered with. That is why the scope description is conditional rather
    than additive.
    """
    from types import SimpleNamespace

    scope = svc.ExportScope.for_release(
        release_id=uuid.uuid4(), project_id=uuid.uuid4(), run_ids=[uuid.uuid4()]
    )
    manifest = _manifest(
        scope,
        run=SimpleNamespace(id=uuid.uuid4()),
        decision=SimpleNamespace(recommendation="GO", risk_score=10, policy_id=None),
    )

    assert set(manifest) == {
        "format_version",
        "generated_at",
        "release_id",
        "project_id",
        "test_run_id",
        "recommendation",
        "risk_score",
        "policy_id",
        "files",
    }, (
        "the release manifest key set changed — every stored manifest_sha256 "
        "now disagrees with a regenerated pack"
    )


def test_a_run_export_manifest_describes_its_scope():
    """The run kind needs what the release kind does not: which runs, and at
    what fidelity. An archive that cannot say what it covers is not evidence."""
    project_id = uuid.uuid4()
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    scope = svc.ExportScope.for_runs(
        project_id=project_id, run_ids=run_ids, tier="summary"
    )

    manifest = _manifest(scope)

    assert manifest["scope"] == "runs"
    assert manifest["tier"] == "summary"
    assert manifest["run_ids"] == [str(r) for r in run_ids]
    assert manifest["project_id"] == str(project_id)
    # And it must NOT claim release provenance it does not have.
    assert "release_id" not in manifest
    assert "recommendation" not in manifest


def test_the_two_kinds_do_not_share_a_key_set():
    """If they did, a consumer could not tell a compliance pack from a
    retention archive, and the tier a run export was taken at would be
    invisible."""
    release = svc.ExportScope.for_release(
        release_id=uuid.uuid4(), project_id=uuid.uuid4(), run_ids=[uuid.uuid4()]
    )
    runs = svc.ExportScope.for_runs(
        project_id=uuid.uuid4(), run_ids=[uuid.uuid4()], tier="full"
    )

    assert set(_manifest(release)) != set(_manifest(runs))


def test_two_manifests_of_unchanged_data_are_byte_identical():
    """Deterministic serialisation, excluding the generation timestamp.

    Two exports of the same data must be diffable; a manifest that reordered
    keys or re-serialised floats differently would make every export look like
    a change.
    """
    scope = svc.ExportScope.for_runs(
        project_id=uuid.uuid4(), run_ids=[uuid.uuid4()], tier="summary"
    )
    at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    files = {"b.json": b"bbb", "a.json": b"a"}

    first = svc._build_manifest(files, at, scope, None, None)
    second = svc._build_manifest(dict(reversed(list(files.items()))), at, scope, None, None)

    assert first == second, (
        "manifest bytes depend on dict insertion order — two exports of "
        "unchanged data would not be diffable"
    )
