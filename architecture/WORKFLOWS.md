# Versioned agent workflows

TestLookup stores project workflows as strict, versioned documents and compiles
published versions into the same LangGraph runtime used by the built-in
pipelines. Open **Agents > Workflows** (`/agents/workflows`) after selecting a
project. Project members can inspect definitions and run validation. QA Leads
can fork, edit, evaluate, publish, create, and delete draft definitions through
the API; the editor exposes the common fork/edit/evaluate/publish path.

## Built-ins and versions

`offline`, `deep`, and `live` are global, read-only templates. Fork one into a
project workflow whose id starts with `wf.` before editing it. A custom
workflow's latest draft is mutable. Publishing makes that version immutable;
editing a published workflow allocates the next draft version instead of
changing execution history.

Pipeline triggers may select a published `workflow_id` and `workflow_version`.
When omitted, the compatible default is `offline@1`. A run freezes the complete
definition, its canonical digest, the compiled plan digest, retry and review
policies, deadline, and safe project agent configurations in
`execution_metadata`. Execution and resume use that snapshot. A version,
definition, plan, project, configuration, or workflow-reference mismatch
refuses checkpoint restore.

## Definition format

A definition contains:

- `workflow_id`, `name`, optional `description`, and a built-in `base`;
- one or more `steps`, each with an id, registered `agent_id`, optional matching
  `config_ref`, tools, reviewer targets, and optional model metadata;
- `edges` whose `from` value is one step or a list of steps, whose `to` value is
  a step or `__end__`, and which may declare a typed `when` condition or
  `join: all|any`;
- explicit bounded `loops` with `from`, `to`, `when`, and `max_iterations`;
- a retry policy, a human review policy, and a deadline.

The compiler rejects unknown capabilities and registered capabilities without
a concrete top-level workflow executor, missing upstream
dependencies on any path, ordinary cycles, invalid reviewer targets, tools or
permissions outside the resolved project configuration, and incomplete
conditional branches. Conditions use a bounded JSON AST: boolean composition
and typed comparison, count, null, boolean, and `else` leaves over the published
workflow state or frozen configuration. The compiler limits nesting, leaf
count, enum values, and membership lists. Missing numeric facts make a branch
condition false instead of failing the run. Back-edges must be declared as
loops with a finite iteration limit. Reviewer steps require one retry loop to a
reviewed step, keyed to the supervisor's `retry` route and bounded to one retry;
a final rejection stops graph execution with `validation_failed`.

Use **Validate** before evaluation. The topology preview is bounded and does not
replace server-side semantic validation.

## Evaluation and publication

G4 workflow evaluation replays measured evidence and reports verdict, sample
count, expected and measured steps, coverage, and regressions. Publishing an
unmeasured draft first runs the minimum evaluation. An
`insufficient_samples` result may publish with its coverage visible, but cannot
be selected as the project default. Cached step outputs do not prove branch,
join, loop, or reviewer routing, so definitions using those controls are
reported as topology-unmeasured instead of receiving a false replay pass. A
measured `fail` requires a QA Lead to explicitly accept the regression and
record a reason; the acceptance is stored with the immutable version.

Publication is compare-and-publish: the client supplies the selected version
and canonical definition digest. If the draft changed after validation, the
server returns 409 and requires a reload. Evaluation cannot rewrite a published
version, and an exact repeated publish is read-only and idempotent.

The project-scoped API is rooted at
`/api/v1/projects/{project_id}/workflows` and supports list, create, get,
update, delete, validate, evaluate, publish, and fork operations. Reads require
project membership. Mutations require the QA Lead project role. Built-in
mutation returns HTTP 405.

## Keeping built-ins equivalent

The live hand-built graphs remain during the compatibility period. The dynamic
regression in `backend/tests/test_workflow_compiler.py` compiles all three
built-in definitions with the real node executors and compares their complete
node, edge, and conditional-branch topology with the live builders. The
pure-standard-library `workflows.builtins-match-compiled` guard pins that test's
collection and strength in the lightweight quality job.

Run both layers after changing a built-in, compiler, or live graph:

```powershell
python scripts/quality_gate.py --only workflows.builtins-match-compiled
$env:PYTHONPATH = "backend"
python -m pytest -q -p no:testlookup backend/tests/test_workflow_compiler.py
```

The editor currently represents condition ASTs and other advanced schema fields
as validated JSON rather than dedicated form controls. Reviewer steps receive
upstream outputs and authoritative run facts, but artifact references remain
empty until workflow state exposes the canonical artifact-id projection.
