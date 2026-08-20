"""Auto-provisioned accounts must not share a hard-coded password.

``default_qa_lead_service`` created one synthetic QA-lead user per project and
gave every one of them the same password, written as a module constant in an
open-source file::

    DEFAULT_QA_LEAD_PASSWORD = "QaLead@2026!"
    ...
    hashed_password=get_password_hash(DEFAULT_QA_LEAD_PASSWORD),
    is_active=True,
    must_change_password=False,

Three things combined to make that exploitable rather than untidy:

* the accounts are created **automatically**, one per project — 42 of the 50
  users on a measured deployment;
* they are provisioned ``is_active=True``, so they can authenticate; and
* ``must_change_password`` does not block login. It is a prompt flag on the
  login response, so the "rotate it after first login" the comment described
  was never enforced.

Verified against a live deployment before the fix: ``POST /api/v1/auth/login``
with that username and that constant returned **HTTP 200** with a token for
role ``QA_LEAD``, which could then read ``/users``, ``/projects`` and
``/runs``. Anyone who had read the repository could do the same to any
deployment that had ever created a project.

The accounts exist to OWN auto-assignments — server-side work that needs no
login — so they now get a random, immediately-discarded secret. An operator who
wants to use one resets it through ``reset_default_qa_lead_password``, which is
the path the module docstring already described, and gets a value unique to
that reset.

**Filename note.** This file is deliberately not named
``..._credentials.py``: ``.gitignore`` line 15 is a broad ``*credentials*``
glob, so a test with that word in its path is silently excluded from the repo.
The first version of this file was, and the fix shipped unguarded until the
partial commit was noticed. ``git add`` prints a hint and still commits
everything else, so the loss is easy to miss in a chained command.

**Fixing the code does not rotate accounts that already exist.** Deployments
provisioned before this change still hold the old hash until each project's
QA-lead password is reset.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import default_qa_lead_service as svc  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(svc)


def test_the_shared_constant_is_gone():
    assert not hasattr(svc, "DEFAULT_QA_LEAD_PASSWORD"), (
        "a module-level default password is shared by every synthetic account "
        "in every deployment, and these accounts can log in"
    )


def test_no_password_like_literal_remains_in_the_module():
    """Catch a rename as well as the original name.

    Looks for string literals with the shape of a password — mixed case plus a
    digit or symbol — assigned to a module-level NAME. Prose in docstrings is
    excluded by only inspecting assignment right-hand sides.
    """
    offenders = []
    for line in SOURCE.splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*[\"']([^\"']{8,})[\"']\s*$", line.strip())
        if not m:
            continue
        name, value = m.groups()
        looks_secretish = (
            any(c.isupper() for c in value)
            and any(c.islower() for c in value)
            and any(c.isdigit() or not c.isalnum() for c in value)
        )
        if looks_secretish and ("PASSWORD" in name or "SECRET" in name or "TOKEN" in name):
            offenders.append(f"{name} = {value!r}")
    assert not offenders, f"hard-coded credential literals: {offenders}"


def test_provisioning_uses_a_random_secret():
    """The provisioning call must hash something generated, not a constant."""
    provision = inspect.getsource(svc.ensure_default_qa_lead)
    assert "get_password_hash(" in provision
    assert "_unguessable_password()" in provision, (
        "the synthetic account is provisioned with something other than a "
        "freshly generated secret"
    )


def test_generated_secrets_are_unique_and_long():
    first, second = svc._unguessable_password(), svc._unguessable_password()
    assert first != second, "generated passwords repeat"
    # token_urlsafe(32) is ~43 chars; anything short enough to guess or
    # brute-force offline defeats the point.
    assert len(first) >= 24


def test_a_blank_reset_does_not_reuse_one_value():
    """The reset helper returns the password it applied. That is deliberate —
    the operator needs it — but two blank resets must not produce the same
    string, or the constant is simply back under another name."""
    reset = inspect.getsource(svc.reset_default_qa_lead_password)
    assert "_unguessable_password()" in reset, (
        "a blank reset re-applies a shared value instead of generating one"
    )


def test_the_account_is_flagged_for_rotation():
    """Defence in depth: it does not block login by itself, but an operator who
    adopts the account is prompted rather than silently inheriting a secret."""
    provision = inspect.getsource(svc.ensure_default_qa_lead)
    assert "must_change_password=True" in provision
