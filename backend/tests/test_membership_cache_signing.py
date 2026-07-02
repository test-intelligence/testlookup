"""Regression: the project-membership Redis cache must be tamper-evident.

Security finding (F3): ``get_accessible_project_ids`` returned the project IDs
read from the ``membership:{user_id}`` Redis key as the request's authorization
scope *without* revalidating against Postgres on a hit. Anyone able to write
Redis could set ``membership:{their_id}`` to arbitrary project UUIDs and read
across tenants, defeating the ``project_id`` WHERE-clause isolation.

The cache values are now HMAC-signed (keyed by APP_SECRET_KEY, bound to the
user id). A poisoned, forged, cross-user, or legacy/plain entry fails
verification and is treated as a cache miss — the DB stays authoritative — so
the cache can never *widen* a user's scope even if Redis is writable.
"""
from __future__ import annotations

import json
import uuid

from app.core.deps import (
    _membership_signature,
    _sign_membership_cache,
    _verify_membership_cache,
)


def test_signed_value_round_trips():
    uid = uuid.uuid4()
    pids = {uuid.uuid4(), uuid.uuid4()}
    signed = _sign_membership_cache(uid, pids)
    assert _verify_membership_cache(uid, signed) == pids


def test_signed_value_round_trips_from_bytes():
    """Redis returns bytes — the verifier must decode them."""
    uid = uuid.uuid4()
    pids = {uuid.uuid4()}
    signed = _sign_membership_cache(uid, pids).encode("utf-8")
    assert _verify_membership_cache(uid, signed) == pids


def test_tampered_payload_is_rejected():
    """Adding a project id to a validly-signed blob breaks the HMAC."""
    uid = uuid.uuid4()
    signed = _sign_membership_cache(uid, {uuid.uuid4()})
    blob = json.loads(signed)
    # Attacker widens scope by appending a project id, keeping the old sig.
    blob["p"] = json.dumps([str(uuid.uuid4()), str(uuid.uuid4())])
    assert _verify_membership_cache(uid, json.dumps(blob)) is None


def test_forged_signature_is_rejected():
    uid = uuid.uuid4()
    poisoned = json.dumps({"p": json.dumps([str(uuid.uuid4())]), "s": "deadbeef"})
    assert _verify_membership_cache(uid, poisoned) is None


def test_cross_user_replay_is_rejected():
    """A blob validly signed for user A must NOT verify under user B's key —
    the signature is bound to the user id."""
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    signed_for_a = _sign_membership_cache(user_a, {uuid.uuid4()})
    assert _verify_membership_cache(user_a, signed_for_a) is not None
    assert _verify_membership_cache(user_b, signed_for_a) is None


def test_legacy_plain_list_is_rejected():
    """A pre-signing value (a bare JSON array) has no signature → treated as a
    miss so the DB re-populates it in the new signed format."""
    legacy = json.dumps([str(uuid.uuid4()), str(uuid.uuid4())])
    assert _verify_membership_cache(uuid.uuid4(), legacy) is None


def test_garbage_is_rejected():
    for bad in ("not-json", "", "{}", json.dumps({"p": "not-a-list", "s": "x"})):
        assert _verify_membership_cache(uuid.uuid4(), bad) is None


def test_signature_is_user_bound():
    """Sanity: the raw signature differs per user for the same payload."""
    payload = json.dumps([str(uuid.uuid4())])
    assert _membership_signature(uuid.uuid4(), payload) != _membership_signature(
        uuid.uuid4(), payload
    )
