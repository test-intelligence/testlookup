# Worker launch and readiness

The supported Compose and Kubernetes worker profiles start through a small standard-library launcher:

```sh
python -m app.worker.worker_process celery -A app.worker.celery_app worker [existing worker options]
```

The launcher preserves the Celery arguments, queues and concurrency, then uses `exec` so Celery remains the container's main process and receives signals directly. Beat keeps its separate launch/probe contract.

Before importing Celery or application task modules, the launcher clears the previous readiness marker and resets regular `.db` files in that worker's multiprocess metric directory. It keeps other files. A generation record identifies the current parent by PID, Linux process start time, PID namespace and boot ID. `worker_ready` publishes readiness only for that prepared generation after the consumer connects and starts. The later `celeryd_init` hook preserves the launcher's reset and any current parent metric files already opened during imports.

The probes import only the standard library and read bounded local state and `/proc`:

```sh
python -m app.worker.worker_process probe --mode=ready
python -m app.worker.worker_process probe --mode=alive
```

| Phase | Readiness | Liveness |
| --- | --- | --- |
| Old marker exists before the replacement launcher runs | Rejected for a dead/reused parent or different namespace/start identity | Rejected for that old identity |
| Launcher initializes the current generation | False | Becomes true after exec into the worker |
| Task modules load or the broker remains unavailable | False | True while the current parent lives |
| Current `worker_ready` signal | True | True |
| Warm shutdown starts and children drain | False | True |
| Parent shutdown completes | Owned state is removed | False |
| SIGKILL/OOM leaves files behind | Retained state fails the current-process check | False for the dead parent |

An old parent or prefork child cannot remove another generation's readiness. A live sibling owning the same state path causes startup to fail before its metrics are cleared. State writes are atomic, state transitions use a short kernel-owned lock, and a crash releases that lock. Startup fails with a finite diagnostic if its required state or metric directory cannot be prepared. A later readiness-write failure leaves the consumer unready and preserves normal signal handling.

Keep `/tmp` and `PROMETHEUS_MULTIPROC_DIR` private to one worker parent and its children. The reference Linux profiles execute the worker as PID 1, which is the probe default. Custom supervisors or shared process/state layouts need an explicit parent-identity design. The `--pid` option supports the isolated native fixture and diagnostic checks of a known parent.

Readiness records consumer startup. Liveness records parent existence. Continue using queue progress, broker health, worker metrics and operational alerts to assess ongoing work. The probes make no broker control-channel request, which keeps a saturated prefork pool from being judged by an `inspect ping` response deadline. Kubernetes liveness executes the helper directly, so a shell's own `pgrep` command cannot satisfy it.

## Image and profile compatibility

Both backend build stages copy the worker helper with the application. Deploy the launcher/probe manifests with the image built from the same candidate source. Images from before this helper existed require their matching earlier profile. Rebuild the source image before using the revised development profile, and use the release manifest's immutable image set for a release profile.

The existing `celeryd_init` fallback supports ad hoc direct Celery launches. Use the reference launcher for the pre-import cleanup guarantee. Keep complete image/profile pairs together during upgrade and rollback. The queue arguments remain in the original Celery form, so offline topology checks and the native Compose queue verifier retain their subscriptions and child-worker attribution.

## Verification

The focused regressions cover stale plain markers, PID/start/namespace/boot changes, generation ownership, live sibling protection, early cleanup, current metric mapping preservation, warm drain, graceful shutdown and all supported Compose worker commands. Podman healthcheck tests verify the new exec-form probe arguments retain their token boundaries.

`backend/tests/regression/native_worker_readiness.py` runs only with the explicit disposable flag as PID 1 in an isolated Linux container. It launches a lightweight Celery app with two native prefork children and supplies the current helper through a read-only bind. It retains `/tmp` across a killed worker process and its replacement, checks consumer readiness, and verifies the old counter resets once while the current parent mmap remains visible. A held prefork task proves warm shutdown clears readiness while the parent drains.

The optional `--real-broker` mode leaves the replacement's loopback broker unavailable until the fixture reports an actual connection failure and repeated NotReady/alive observations. The controller can then start a pinned Valkey container using `--network container:<proof-container>` and `--bind 127.0.0.1`. The proof container itself keeps `--network none`. Recovery must produce consumer readiness, prefork task consumption and graceful cleanup. Give both containers unique owned names, use `--pull never`, publish no ports and remove only those owned resources after the test.

The native witness used Python 3.14.10, Celery 5.7.0, prometheus-client 0.26.0 and a pinned Valkey 8.1.12 runtime. All twelve lifecycle/recovery checks passed with a maximum observed probe runtime below 0.24 seconds under a one-CPU limit. This was a current-helper fixture on an existing backend image. It provides worker lifecycle and library compatibility evidence. Full candidate-image application/queue and deployment acceptance remain part of the release verification process.
