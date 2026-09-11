"""Re-audit N20: an ADMIN check is closed to a project-bound API key unless reviewed.

Only an ADMIN can bind an API key to a project, so nearly every project-bound
key is an ADMIN's CI credential. ``require_role(UserRole.ADMIN)`` compared the
key OWNER's role, so a key leaked from one team's pipeline could create an
instance administrator. It now refuses such a key unless the route passes
``allow_project_key=True``. Two ways back to the bug remain, and this file
closes both, scanning ``backend/app`` source as AST, not text:

1. **An opt-in nobody reviewed.** Every
   ``require_role(UserRole.ADMIN, allow_project_key=True)`` must be listed in
   ``REVIEWED_OPT_INS`` with the check that confines the key to its own
   project, and that check must actually appear in the handler. A new opt-in
   fails here until someone reads the route and lists it.

2. **An ADMIN check written by hand.** A function that receives the request
   principal and grants or gates on ADMIN itself bypasses the default:

   * grant: an ``if`` whose test says "is ADMIN" and whose body returns (or
     whose ``else`` raises), the early return that skipped the project check
     in the investigation, fix-attempt, chat, share-link and key-owner guards;
   * gate: an ``if`` whose test says "is not ADMIN" and whose body raises
     (or whose ``else`` returns), an inline ``require_role(ADMIN)``.

   "Says ADMIN" is a comparison with ``UserRole.ADMIN``, ``UserRole.ADMIN.value``
   or ``"ADMIN"`` (``==``/``is``, or ``!=``/``is not``), directly, inside
   ``and``/``or``/``not``, or through a local name assigned from one
   (``is_admin = ...``). Such an ``if`` passes only when the function consults
   the binding (``_api_key_bound_project`` / ``_enforce_api_key_project_binding``)
   at or before its test, or when it is listed in ``REVIEWED_INLINE_ADMIN_CHECKS``.

What scan 2 deliberately does not catch, so nobody reads more into it:

* a function that does not take the principal as a parameter (named
  ``current_user``/``user``/``actor_user``/... or annotated ``User``). A user
  loaded from the database by id carries no binding, so there is nothing to
  consult: the password login, the SSE handshake's JWT, a suite owner being
  assigned;
* role-SET checks (``role in (QA_LEAD, ADMIN)``), which are "lead or above",
  not an ADMIN check;
* a check not written as an ``if`` (``return role == ADMIN or member``);
* order, not data flow: any binding reference earlier in the function exempts
  it, whether or not it guards the same project.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"

#: Checks that confine a project-bound key to one project. An opt-in must name
#: one of them, and it must appear in the handler (decorators, signature, body).
BINDING_CHECKS: frozenset[str] = frozenset({
    "require_project_access",   # path {project_id}; deps.py compares it to the binding
    "require_run_access",       # path {run_id} -> the run's project
    "require_release_access",   # path {release_id} -> the release's project
    "resolve_project_scope",    # a bound key's accessible set is {its project}
    "_enforce_api_key_project_binding",
    "_enforce_policy_binding",  # routers/release_gate_policies.py, wraps the one above
    "get_accessible_project_ids",  # a bound key's set is {its project}; the handler filters by it
    # Router-local helpers that call one of the above on the resource's project:
    "_load_and_scope",          # routers/webhooks_outbound.py -> resolve_project_scope
    "_enforce_project_access",  # routers/suites.py, test_management_suite_reviews.py -> get_accessible_project_ids
})

#: (file, handler) -> (binding check, one-line reason). Read the route before adding.
REVIEWED_OPT_INS: dict[tuple[str, str], tuple[str, str]] = {
    ("app/routers/compliance_packs.py", "retire_compliance_pack"): (
        "resolve_project_scope", "called on the pack's own project_id before the retire"),
    ("app/routers/compliance_packs.py", "delete_compliance_pack"): (
        "resolve_project_scope", "called on the pack's own project_id before the delete"),
    ("app/routers/ownership.py", "bulk_import_rules"): (
        "require_project_access", "path {project_id}; rules are written for that project only"),
    ("app/routers/projects.py", "reset_project_data"): (
        "require_project_access", "path {project_id}; resets that project only"),
    ("app/routers/releases.py", "delete_release"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/releases.py", "delete_phase"): (
        "require_release_access", "path {release_id}; the phase must belong to that release"),
    ("app/routers/releases.py", "unlink_test_run"): (
        "require_release_access", "both path ids checked, release and run (require_run_access)"),
    ("app/routers/retention.py", "put_retention_policy"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/retention.py", "get_project_storage"): (
        "require_project_access", "path {project_id}; that project's footprint only"),
    ("app/routers/retention.py", "list_deletion_jobs"): (
        "require_project_access", "path {project_id}; lists that project's jobs"),
    ("app/routers/retention.py", "get_deletion_job"): (
        "require_project_access", "path {project_id}; another project's job is a 404"),
    ("app/routers/retention.py", "preview_retention_purge"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/retention.py", "enqueue_retention_purge"): (
        "require_project_access", "path {project_id}; the purge is queued for it only"),
    ("app/routers/retention.py", "preview_criteria_deletion"): (
        "require_project_access", "path {project_id}; foreign run ids are refused"),
    ("app/routers/retention.py", "execute_criteria_deletion"): (
        "require_project_access", "path {project_id}; the frozen job must be that project's"),
    ("app/routers/runs.py", "delete_run"): (
        "require_run_access", "path {run_id} resolved to its project"),
    ("app/routers/users.py", "remove_project_member"): (
        "require_project_access", "path {project_id}; the membership row is filtered by it"),
    ("app/routers/release_gate_policies.py", "create_policy"): (
        "_enforce_policy_binding", "body project_id; the system default (None) is refused"),
    ("app/routers/release_gate_policies.py", "update_policy"): (
        "_enforce_policy_binding", "the stored policy's project; system default refused"),
    ("app/routers/release_gate_policies.py", "publish_policy"): (
        "_enforce_policy_binding", "the stored policy's project; system default refused"),
    ("app/routers/release_gate_policies.py", "deactivate_policy"): (
        "_enforce_policy_binding", "the stored policy's project; system default refused"),
    ("app/routers/release_gate_policies.py", "simulate_policy"): (
        "_enforce_api_key_project_binding", "against the body run's project"),
    # ── re-audit N26: require_role(UserRole.QA_LEAD, allow_project_key=True) ──
    ("app/routers/projects.py", "update_project"): (
        "require_project_access", "path {project_id}; updates that project only"),
    ("app/routers/projects.py", "delete_project"): (
        "require_project_access", "path {project_id}; soft-deletes that project only"),
    ("app/routers/runs.py", "set_run_release"): (
        "require_run_access", "path {run_id} resolved to its project"),
    ("app/routers/notifications.py", "update_transition_policy"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/deep_investigation.py", "review_defect"): (
        "resolve_project_scope", "called on the defect's own project"),
    ("app/routers/deep_investigation.py", "list_pending_defects"): (
        "get_accessible_project_ids", "the list is filtered to the accessible set"),
    ("app/routers/release_readiness.py", "override_release_decision"): (
        "require_run_access", "path {run_id} resolved to its project"),
    ("app/routers/releases.py", "create_release"): (
        "resolve_project_scope", "called on the body project_id"),
    ("app/routers/releases.py", "sync_releases_from_external"): (
        "resolve_project_scope", "called on the body project_id"),
    ("app/routers/releases.py", "update_release"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/releases.py", "evaluate_release_gate"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/releases.py", "add_phase"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/releases.py", "update_phase"): (
        "require_release_access", "path {release_id}; the phase must belong to that release"),
    ("app/routers/releases.py", "activate_release_endpoint"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/releases.py", "evaluate_release_phase_gate"): (
        "require_release_access", "path {release_id}; the phase must belong to that release"),
    ("app/routers/releases.py", "link_test_run"): (
        "require_release_access", "path {release_id}; the body run must be in the same project"),
    ("app/routers/test_management_suite_reviews.py", "set_suite_owner"): (
        "_enforce_project_access", "called on the named project"),
    ("app/routers/users.py", "add_project_member"): (
        "require_project_access", "path {project_id}; grant ceiling applies"),
    ("app/routers/users.py", "update_project_member_role"): (
        "require_project_access", "path {project_id}; grant ceiling applies"),
    ("app/routers/value_metric_assumptions.py", "put_value_metric_assumptions"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/release_attribution_rules.py", "create_attribution_rule"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/release_attribution_rules.py", "update_attribution_rule"): (
        "require_project_access", "path {project_id}; the rule is filtered by it"),
    ("app/routers/release_attribution_rules.py", "delete_attribution_rule"): (
        "require_project_access", "path {project_id}; the rule is filtered by it"),
    ("app/routers/ownership.py", "create_ownership_rule"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/ownership.py", "update_ownership_rule"): (
        "require_project_access", "path {project_id}; the rule is filtered by it"),
    ("app/routers/ownership.py", "delete_ownership_rule"): (
        "require_project_access", "path {project_id}; the rule is filtered by it"),
    ("app/routers/ownership.py", "import_codeowners"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/ownership.py", "upsert_team_channel"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/ownership.py", "delete_team_channel"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/audit_dashboard.py", "list_audit_events"): (
        "resolve_project_scope", "query project_id, or the accessible set"),
    ("app/routers/audit_dashboard.py", "get_project_observability"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/flaky_quarantine.py", "create_proposal"): (
        "resolve_project_scope", "called on the body project_id"),
    ("app/routers/flaky_quarantine.py", "approve_quarantine"): (
        "resolve_project_scope", "called on the request's own project"),
    ("app/routers/flaky_quarantine.py", "reject_quarantine"): (
        "resolve_project_scope", "called on the request's own project"),
    ("app/routers/flaky_quarantine.py", "release_quarantine"): (
        "resolve_project_scope", "called on the request's own project"),
    ("app/routers/flaky_quarantine.py", "update_quarantine_lifecycle_policy"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/compliance_packs.py", "generate_compliance_pack"): (
        "require_release_access", "path {release_id} resolved to its project"),
    ("app/routers/github_integration.py", "upsert_github_integration"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/github_integration.py", "test_github_integration"): (
        "require_project_access", "path {project_id}; probes that project's own token"),
    ("app/routers/github_integration.py", "delete_github_integration"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/gitlab_integration.py", "upsert_gitlab_integration"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/gitlab_integration.py", "test_gitlab_integration"): (
        "require_project_access", "path {project_id}; probes that project's own token"),
    ("app/routers/webhooks_outbound.py", "create_webhook_subscription"): (
        "resolve_project_scope", "called on the body project_id"),
    ("app/routers/webhooks_outbound.py", "update_webhook_subscription"): (
        "_load_and_scope", "the subscription's own project"),
    ("app/routers/webhooks_outbound.py", "delete_webhook_subscription"): (
        "_load_and_scope", "the subscription's own project"),
    ("app/routers/webhooks_outbound.py", "test_webhook_subscription"): (
        "_load_and_scope", "the subscription's own project"),
    ("app/routers/webhooks_outbound.py", "replay_webhook_delivery"): (
        "_load_and_scope", "the subscription's own project"),
    ("app/routers/suites.py", "delete_suite"): (
        "_enforce_project_access", "called on the suite's own project"),
    ("app/routers/suites.py", "set_default"): (
        "_enforce_project_access", "called on the suite's own project"),
    ("app/routers/suites.py", "confirm_canonical_retirement"): (
        "_enforce_project_access", "called on the canonical case's own project"),
    ("app/routers/agent_actions.py", "list_agent_actions"): (
        "require_project_access", "path {project_id}"),
    ("app/routers/agent_actions.py", "transition_agent_action"): (
        "require_project_access", "path {project_id}; the action is filtered by it"),
}

#: (file, function) -> why its hand-written ADMIN check is not a bypass.
REVIEWED_INLINE_ADMIN_CHECKS: dict[tuple[str, str], str] = {
    ("app/routers/users.py", "_enforce_grant_ceiling"): (
        "both callers (add/update project member) run require_project_access() on the "
        "path project first, so a bound key is already confined to it"
    ),
    ("app/services/notification/manager.py", "digest_owner_block_reason"): (
        "re-audit N33: its user is a digest subscription's owner loaded by id in "
        "dispatch_scheduled_digests, not the request principal, so it carries no API-key "
        "binding to consult; the ADMIN branch keeps the workspace-wide-digest rule"
    ),
}

_BINDING_REFERENCES = frozenset({"_api_key_bound_project", "_enforce_api_key_project_binding"})
_PRINCIPAL_NAMES = frozenset({"current_user", "user", "actor_user", "actor", "caller", "principal"})
IS_ADMIN, NOT_ADMIN = "is-admin", "not-admin"


# ── shared AST helpers ───────────────────────────────────────────────────────


def _sources():
    for path in sorted(APP.rglob("*.py")):
        yield path.relative_to(APP.parent).as_posix(), path.read_text(encoding="utf-8")


def _functions(tree):
    """``(node, dotted qualname)`` for every function, nested ones included."""
    out = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                out.append((child, name))
                visit(child, name)
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _calls_with_owner(tree):
    """Every call, with the function it configures or runs in.

    A call in a handler's decorator or parameter default belongs to that
    handler: that is where ``Depends(require_role(...))`` lives.
    """
    out = []

    def visit(node, owner):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name if owner is None else f"{owner}.{node.name}"
        if isinstance(node, ast.Call):
            out.append((node, owner))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, None)
    return out


#: The roles whose require_role refuses a project-bound key, so the only ones
#: where ``allow_project_key=True`` means anything (N20: ADMIN, N26: QA_LEAD).
_OPT_IN_ROLES = frozenset({"ADMIN", "QA_LEAD"})


def _is_user_role(node, names) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr in names
        and isinstance(node.value, ast.Name)
        and node.value.id == "UserRole"
    )


def _is_user_role_admin(node) -> bool:
    return _is_user_role(node, {"ADMIN"})


def _names_admin(node) -> bool:
    """``UserRole.ADMIN``, ``UserRole.ADMIN.value`` or the literal ``"ADMIN"``."""
    if isinstance(node, ast.Constant):
        return node.value == "ADMIN"
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    return _is_user_role_admin(node)


# ── scan 1: opt-ins ──────────────────────────────────────────────────────────


def find_opt_ins(source: str, rel: str) -> tuple[set[tuple[str, str]], list[str]]:
    """``(file, handler)`` for each ``require_role(UserRole.ADMIN, allow_project_key=True)``,
    and a problem for anything the reviewer could not see."""
    found: set[tuple[str, str]] = set()
    problems: list[str] = []
    for call, owner in _calls_with_owner(ast.parse(source)):
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        keyword = next((k for k in call.keywords if k.arg == "allow_project_key"), None)
        where = f"{rel}:{call.lineno}"
        if name == "require_role" and any(k.arg is None for k in call.keywords):
            problems.append(f"{where}: require_role(**...) hides whether it opts in")
            continue
        if keyword is None:
            continue
        if name != "require_role":
            problems.append(
                f"{where}: allow_project_key passed to {ast.unparse(func)}(), which this "
                "ratchet cannot review; call require_role directly"
            )
            continue
        if not (isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool)):
            problems.append(
                f"{where}: allow_project_key={ast.unparse(keyword.value)} is not a literal, "
                "so no reviewer can see what it allows"
            )
            continue
        if keyword.value.value is False:
            continue
        if not call.args or not _is_user_role(call.args[0], _OPT_IN_ROLES):
            problems.append(
                f"{where}: allow_project_key=True only means something on "
                "require_role(UserRole.QA_LEAD) and require_role(UserRole.ADMIN)"
            )
            continue
        found.add((rel, owner or "<module>"))
    return found, problems


def _scan_opt_ins():
    found: set[tuple[str, str]] = set()
    problems: list[str] = []
    for rel, source in _sources():
        f, p = find_opt_ins(source, rel)
        found |= f
        problems += p
    return found, problems


# ── scan 2: hand-written ADMIN checks ────────────────────────────────────────


def _own_nodes(root):
    """Nodes under ``root``, not descending into nested functions or classes."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _contains(statements, kind) -> bool:
    for statement in statements:
        if isinstance(statement, kind):
            return True
        if any(isinstance(node, kind) for node in _own_nodes(statement)):
            return True
    return False


def _takes_principal(func) -> bool:
    args = func.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        if arg.arg in _PRINCIPAL_NAMES:
            return True
        if arg.annotation is None:
            continue
        for node in ast.walk(arg.annotation):
            if isinstance(node, ast.Name) and node.id == "User":
                return True
            if isinstance(node, ast.Constant) and node.value == "User":
                return True
    return False


def _polarity(test, flags) -> set[str]:
    """Which ways ``test`` depends on the caller being ADMIN."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if _names_admin(test.left) or _names_admin(test.comparators[0]):
            if isinstance(test.ops[0], (ast.Eq, ast.Is)):
                return {IS_ADMIN}
            if isinstance(test.ops[0], (ast.NotEq, ast.IsNot)):
                return {NOT_ADMIN}
        return set()
    if isinstance(test, ast.Name):
        return set(flags.get(test.id, ()))
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return {NOT_ADMIN if p == IS_ADMIN else IS_ADMIN for p in _polarity(test.operand, flags)}
    if isinstance(test, ast.BoolOp):
        out: set[str] = set()
        for value in test.values:
            out |= _polarity(value, flags)
        return out
    return set()


def find_inline_admin_checks(source: str, rel: str) -> set[tuple[str, str]]:
    """``(file, function)`` for each unreviewed hand-written ADMIN grant or gate."""
    found: set[tuple[str, str]] = set()
    for func, qualname in _functions(ast.parse(source)):
        if not _takes_principal(func):
            continue
        own = list(_own_nodes(func))
        flags: dict[str, set[str]] = {}
        for node in own:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                polarity = _polarity(node.value, {})
                if polarity:
                    flags[node.targets[0].id] = polarity
        binding_lines = [
            node.lineno
            for node in own
            if (isinstance(node, ast.Name) and node.id in _BINDING_REFERENCES)
            or (isinstance(node, ast.Attribute) and node.attr in _BINDING_REFERENCES)
        ]
        for node in own:
            if not isinstance(node, ast.If):
                continue
            polarity = _polarity(node.test, flags)
            grants = IS_ADMIN in polarity and (
                _contains(node.body, ast.Return) or _contains(node.orelse, ast.Raise)
            )
            gates = NOT_ADMIN in polarity and (
                _contains(node.body, ast.Raise) or _contains(node.orelse, ast.Return)
            )
            if not (grants or gates):
                continue
            if any(line <= node.test.end_lineno for line in binding_lines):
                continue
            found.add((rel, qualname))
    return found


def _scan_inline_checks() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for rel, source in _sources():
        found |= find_inline_admin_checks(source, rel)
    return found


# ── the ratchets ─────────────────────────────────────────────────────────────


def test_every_admin_opt_in_was_reviewed():
    found, problems = _scan_opt_ins()

    assert not problems, "\n".join(problems)
    unexpected = sorted(found - set(REVIEWED_OPT_INS))
    stale = sorted(set(REVIEWED_OPT_INS) - found)
    assert not unexpected, (
        "require_role(UserRole.ADMIN, allow_project_key=True) lets a project-bound "
        "API key in. Read each route below, confirm it confines the key to its own "
        "project (see BINDING_CHECKS), then add it to REVIEWED_OPT_INS:\n  "
        + "\n  ".join(f"{f}::{h}" for f, h in unexpected)
    )
    assert not stale, (
        "these REVIEWED_OPT_INS entries no longer opt in (or were renamed); delete "
        "them:\n  " + "\n  ".join(f"{f}::{h}" for f, h in stale)
    )


def test_each_reviewed_opt_in_runs_the_check_it_names():
    """A reason is a claim; the named check must actually be in the handler."""
    for (rel, handler), (check, reason) in sorted(REVIEWED_OPT_INS.items()):
        assert check in BINDING_CHECKS, f"{rel}::{handler}: {check} is not a binding check"
        assert reason.strip() and chr(10) not in reason, f"{rel}::{handler}: one-line reason"
        tree = ast.parse((APP.parent / rel).read_text(encoding="utf-8"))
        nodes = [node for node, name in _functions(tree) if name == handler]
        assert nodes, f"{rel}::{handler} not found"
        assert check + "(" in ast.unparse(nodes[0]), (
            f"{rel}::{handler} opts a project-bound key in but no longer calls {check}()"
        )


def test_no_hand_written_admin_check_skips_the_binding():
    found = _scan_inline_checks()

    unexpected = sorted(found - set(REVIEWED_INLINE_ADMIN_CHECKS))
    stale = sorted(set(REVIEWED_INLINE_ADMIN_CHECKS) - found)
    assert not unexpected, (
        "these functions decide on the ADMIN role themselves, before consulting the "
        "API-key binding, so a project-bound admin key gets every tenant. Use "
        "require_role(UserRole.ADMIN) or call _enforce_api_key_project_binding "
        "before the check:\n  " + "\n  ".join(f"{f}::{q}" for f, q in unexpected)
    )
    assert not stale, (
        "these REVIEWED_INLINE_ADMIN_CHECKS entries no longer match; delete them:\n  "
        + "\n  ".join(f"{f}::{q}" for f, q in stale)
    )


# ── self-tests: the scanners catch what they claim ───────────────────────────


def _opt_ins(code):
    return find_opt_ins(textwrap.dedent(code), "mem.py")


def _inline(code):
    return {q for _f, q in find_inline_admin_checks(textwrap.dedent(code), "mem.py")}


def test_scan1_finds_an_opt_in_in_a_parameter_default():
    found, problems = _opt_ins("""
        @router.delete("/{run_id}")
        async def handler(user=Depends(require_role(UserRole.ADMIN, allow_project_key=True))):
            pass
    """)
    assert found == {("mem.py", "handler")} and not problems


def test_scan1_finds_an_opt_in_in_route_dependencies():
    found, _ = _opt_ins("""
        @router.post("/x", dependencies=[Depends(require_role(UserRole.ADMIN, allow_project_key=True))])
        async def handler():
            pass
    """)
    assert found == {("mem.py", "handler")}


def test_scan1_finds_a_router_wide_opt_in():
    found, _ = _opt_ins("""
        router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN, allow_project_key=True))])
    """)
    assert found == {("mem.py", "<module>")}


def test_scan1_finds_a_qa_lead_opt_in():
    """Re-audit N26: QA_LEAD refuses a bound key too, so its opt-ins are reviewed."""
    found, problems = _opt_ins("""
        async def handler(user=Depends(require_role(UserRole.QA_LEAD, allow_project_key=True))):
            pass
    """)
    assert found == {("mem.py", "handler")} and not problems


def test_scan1_ignores_the_default_and_an_explicit_false():
    found, problems = _opt_ins("""
        async def a(user=Depends(require_role(UserRole.ADMIN))):
            pass
        async def b(user=Depends(require_role(UserRole.ADMIN, allow_project_key=False))):
            pass
    """)
    assert found == set() and problems == []


@pytest.mark.parametrize("code", [
    "require_role(UserRole.ADMIN, allow_project_key=FLAG)",
    "require_role(UserRole.QA_ENGINEER, allow_project_key=True)",
    "rr(UserRole.ADMIN, allow_project_key=True)",
    "require_role(UserRole.ADMIN, **opts)",
], ids=["not-a-literal", "below-qa-lead", "aliased-call", "splatted"])
def test_scan1_reports_what_it_cannot_review(code):
    found, problems = _opt_ins(f"async def h(user=Depends({code})):\n    pass\n")
    assert found == set() and len(problems) == 1


def test_scan2_flags_the_pre_n20_guard_shape():
    """The investigation guard as it was: row resolved, then ADMIN returned early."""
    assert _inline("""
        def require_x():
            async def _check(request, db, current_user):
                project_id = await lookup(db)
                role_value = getattr(current_user.role, "value", current_user.role)
                if str(role_value) == UserRole.ADMIN.value:
                    return current_user
                await membership(db, current_user, project_id)
            return _check
    """) == {"require_x._check"}


def test_scan2_follows_an_alias_a_literal_and_or():
    """The share-link guard as it was."""
    assert _inline("""
        async def guard(db, current_user):
            is_admin = current_user.role == "ADMIN"
            if is_admin or link.created_by_id == current_user.id:
                return link
    """) == {"guard"}


@pytest.mark.parametrize("body", [
    "if current_user.role != UserRole.ADMIN.value:\n        raise HTTPException(403)",
    "is_admin = current_user.role == UserRole.ADMIN\n    if not is_admin:\n        raise HTTPException(403)",
    "if current_user.role != UserRole.ADMIN:\n        member = await lookup()\n        if member is None:\n            raise HTTPException(403)",
    "if current_user.role == UserRole.ADMIN:\n        pass\n    else:\n        raise HTTPException(403)",
], ids=["inline-require-role", "negated-alias", "raise-nested-in-body", "raise-in-else"])
def test_scan2_flags_an_inline_admin_gate(body):
    assert _inline(f"async def handler(current_user: User):\n    {body}\n") == {"handler"}


def test_scan2_accepts_the_binding_consulted_first():
    assert _inline("""
        async def guard(db, current_user, project_id):
            _enforce_api_key_project_binding(current_user, project_id)
            if current_user.role == UserRole.ADMIN:
                return current_user
    """) == set()


def test_scan2_rejects_the_binding_consulted_after_the_return():
    assert _inline("""
        async def guard(db, current_user, project_id):
            if current_user.role == UserRole.ADMIN:
                return current_user
            _enforce_api_key_project_binding(current_user, project_id)
    """) == {"guard"}


def test_scan2_does_not_let_an_outer_function_vouch_for_an_inner_one():
    assert _inline("""
        def factory(current_user):
            _api_key_bound_project(current_user)
            async def _check(current_user):
                if current_user.role == UserRole.ADMIN:
                    return current_user
            return _check
    """) == {"factory._check"}


def test_scan2_documented_limits_stay_limits():
    """Out of scope by design (see the module docstring), pinned so a change is deliberate."""
    assert _inline("""
        async def login(form_data, db):
            user = await load(db, form_data.username)
            if user.role == UserRole.ADMIN:
                return token(user)
        async def team(current_user):
            if current_user.role in (UserRole.QA_LEAD, UserRole.ADMIN):
                return everything()
        async def flag_only(current_user):
            if current_user.role == UserRole.ADMIN:
                count += 1
    """) == set()


def test_the_scans_read_the_real_tree():
    """A scan that parsed nothing would pass forever."""
    found, _ = _scan_opt_ins()
    assert len(found) >= 20, found
    assert any(rel == "app/core/deps.py" for rel, _ in _sources())
