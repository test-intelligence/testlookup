# Disposable monitored candidate proof

The opt-in monitored harness starts a separate private Compose project for one
immutable candidate, observes normal application signals and removes its scoped
resources. It reuses ordinary release preparation with browser/provider fixtures
disabled. Preparation starts no container or fake TLS initializer. Application
image references come from the verified manifest. Monitoring, telemetry and
metrics-auth configuration comes from `git archive` at that manifest's source
SHA. Site `.env` files are never copied.

The monitored lane keeps offline mode enabled and uses a normal `run_ai:false`
ingest. It needs no provider endpoint, Jira fixture or fixture CA. The ordinary
release harness preserves its existing fixture-enabled default.

Run on a dedicated Linux amd64 container engine with Python, PyYAML, Git, Podman
or Docker, a current Docker Compose provider supporting `!override`/`!reset`,
and Docker buildx for the existing image inspector's registry-manifest reads.
The candidate image must contain the signal checker and its required runtime
features. A prior candidate missing those committed inputs remains blocked when
this optional lane is requested.

```sh
python e2e-runner/monitored_ephemeral.py --engine podman --manifest verified/release-manifest.json --out release-results/signal-gate.json
```

`--out` must name a fresh JSON file. `--startup-timeout` defaults to 600 seconds and
accepts 30 to 1800 seconds. The ordinary release campaign continues to use its
existing path unless its caller explicitly opts in to this separate lane. Run
it only against a dedicated disposable engine/context. Its generated
`tl-e2e-*` project never reuses an existing application deployment or database.

## Configuration and trust boundaries

The harness archives a fixed input list at the exact candidate SHA and records
the archive hash. It rejects missing inputs, nonregular archive members and
path traversal. The current orchestration/seed/preflight/scheduler helpers have
a separate digest in the result. This separates operator tooling identity from
the archived candidate's signal checker and runtime image identity.

The generated release stack is layered with the committed monitoring,
telemetry and metrics-auth overlays, then rendered through the actual Compose
CLI. [Compose merges environments and volumes](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)
and resolves relative paths against the first file. The harness keeps that
base in private scratch, copies the archived inputs there and validates the
resolved bind and secret sources before starting the stack.

The final test profile removes all published ports and fixed container names.
Every network is internal and uniquely named. External networks/volumes,
nonlocal storage options, host/container network modes, privileged services,
floating images and binds outside the private directory fail configuration
validation. API, worker, worker-children and MCP retain only the private
application and telemetry-ingress bridges. They gain no access to telemetry
storage/query bridges. The harness never overrides image version or build
revision to make candidate checks pass.

Credentials are generated for this private stack. Separate read-only metrics
token files serve the API and Prometheus runtime UIDs, under a 0700 host directory.
The expanded Compose configuration, API key and generated environment stay in
temporary scratch and are excluded from public evidence. No Docker socket or
host logs are mounted. Image pulls may populate the operator engine's ordinary
image cache, which cleanup deliberately preserves.

## Normal fixture and signal flow

The existing bootstrap completes the fresh administrator's forced password
reset inside the private backend. The seed then uses normal authenticated HTTP
routes to create a `release-signal-*` project, resolve a synthetic QA_ENGINEER,
grant that project's membership and mint a one-day project-bound key with only
`project:write` and `stream:write`. The gate receives the key through a private
environment file. It never appears in the engine command arguments or reports.

A real Beat service runs the candidate's existing
`app.worker.tasks.relay_run_downstream_outbox` schedule entry. The selected entry
retains its source-defined name, interval and routing options. Other periodic
maintenance and automatic default entries are excluded. The scheduler state is
temporary. [Celery documents Beat's periodic scheduling](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)
and requires one scheduler per schedule. This profile uses one Beat container.
The fixture does not mark outbox rows complete or accelerate the relay interval.

The normal JSON ingest producer stages its required downstream operations in the
durable outbox. The real relay and workers must deliver and finish those
operations. Without that scheduler path, the rows remain pending and the signal
checker cannot pass.

The host runs
[the existing candidate inspector](../../scripts/monitoring/inspect_compose_candidate.py)
to prove four application roles use the declared immutable references and
verified platform/config digests, and all four named bridges are internal.
The checker runs in a separate container using the same candidate backend
digest and its locked Celery dependencies. That one-off container joins only
the application, telemetry storage and telemetry metrics bridges. It has a
read-only root, no capabilities, a private temporary filesystem and bounded
memory/CPU/PIDs. The main application containers retain their narrower network
boundary. On Linux, rootless Podman uses `--userns=keep-id` with the caller UID/GID
so the one-off checker can read its private bind inputs and write its proof.

The preflight reads fixed private readiness endpoints and waits for the three
application scrape targets. It then invokes
[the archived signal checker](../../scripts/monitoring/release_signal_gate.py).
That checker owns the exact API/worker metric deltas, two real queue consumer
receipts, one synthetic two-result ingest, normal durable completion,
correlated logs/traces, MCP operational logging and fresh collector assertions.
It does not inject telemetry directly into Loki or Jaeger. Receiver
firing/recovery, browser/MCP tool journeys, restore and load acceptance remain
separate checks.

## Evidence, failures and cleanup

The public JSON result records the candidate/source, manifest/archive and
orchestrator hashes, profile, generated project identity, status and cleanup
outcome. It writes the project identity before containers can start so an
interrupted run has a known reconciliation target. Sibling
`signal-gate-evidence/` holds the sanitized runtime and signal proofs when
produced. Failed child proofs are preserved as evidence too.

The harness always attempts to remove its one-off checker and run Compose down
with volumes and orphans. It checks project-labelled containers, volumes and
networks afterward. Exit 0 requires a passed signal checker and completed cleanup.
Startup, seed, identity, checker and cleanup failures return nonzero. A process
kill or unavailable engine can prevent teardown. Use the recorded generated
project identity to inspect and reconcile only that run's resources, then retain
the cleanup outcome with its proof. No success is inferred from a config render,
healthy process or missing result.

Offline tests exercise seed authority, archive scope, network/storage bounds,
normal relay selection and all started failure paths. A real Compose config
render validates overlay compatibility without starting services. These results
establish orchestration contracts. A full immutable candidate run remains
required for actual startup, secret-file readability across runtime UIDs,
readiness, normal Beat delivery, MCP SDK behavior, every signal assertion and
observed teardown. The local source tests do not supply that deployed proof.
