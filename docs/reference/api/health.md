# Health API

[Documentation home](../../README.md) · [Regeneration](../../handoff/maintenance.md)

Generated from tracked source by `scripts/generate_handoff_reference.py`. Do not edit by hand. The baseline and validation limits are recorded in [verification](../../handoff/verification.md).

## GET `/health/details`

Full dependency status — for ops dashboards

Full health report across all infrastructure dependencies.
Runs all checks concurrently; returns 200 even when non-critical services are degraded.
Not intended for K8s probes — use /health/live and /health/ready instead.

Every probe runs under a wall-clock budget, so no single dependency can
stall the whole response: optional ones via ``_with_budget`` (reported
``degraded``), critical ones via ``_critical`` (reported ``error``, so
readiness still fails closed on them).

The critical probes were previously left unwrapped, on the reasoning that
the readiness probe needs their honest result. A timeout *is* an honest
failure, and leaving them unbounded cost more than it bought: an
unreachable Redis blocks for its 5s socket timeout, which made this
endpoint take 5.01s and put /health/ready in a dead heat with the
kubelet's own 5s deadline -- and a kubelet timeout discards the body that
says which dependency died.

The optional probes resolve their endpoints with ``use_cache=False``.
That cache is Redis-backed, and ``except Exception`` around it catches a
refusal but cannot shorten a hang, so a Redis outage used to time out the
MinIO and ChromaDB probes and report both ``degraded`` while both were
healthy.

Source: [backend/app/routers/health.py:291](../../../backend/app/routers/health.py#L291).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "health_details_health_details_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'status': 'healthy' if critical_ok else 'degraded', 'version': settings.APP_VERSION, 'build': build_provenance(), 'env': settings.APP_ENV, 'uptime_seconds': int(time.time() - _START_TIME), 'timestamp': datetime.now(timezone.utc).isoformat(), 'checks': {'postgres': pg, 'mongo': mongo, 'redis': redis, 'minio': minio, 'ollama': ollama, 'chromadb': chroma}}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/health/ingestion`

Live-stream ingestion health — gate + queue + memory

Surface the load + backpressure signals used by the live-stream
admission gates.

The output is consumed by the future Grafana panel (Phase 1 of the
scalable-ingestion plan in ``docs/SCALABLE_INGESTION_DESIGN.md``)
and by support / on-call humans when a customer reports "my runs
are slow / 429ing". A single GET answers:

* Is Redis approaching the memory threshold that triggers 503?
* What's the per-minute ingest vs reject rate?
* How many live sessions are active right now?
* How deep are the Celery queues backing ingestion + AI analysis?
* What thresholds is the gate using today (env-tuned, useful for
  verifying the deployment matches the docs)?

Everything is best-effort — a degraded Redis returns partial data
rather than 500'ing the health endpoint itself.

Source: [backend/app/routers/health.py:344](../../../backend/app/routers/health.py#L344).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "health_ingestion_health_ingestion_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {
            "additionalProperties": true,
            "title": "Response Health Ingestion Health Ingestion Get",
            "type": "object"
          }
        }
      },
      "description": "Successful Response"
    }
  }
}
```

## GET `/health/live`

Liveness probe — is the process alive?

Kubernetes liveness probe.
Returns 200 as long as the process is running and the event loop is responsive.
Never checks external dependencies — a database outage must NOT restart the pod.

Source: [backend/app/routers/health.py:225](../../../backend/app/routers/health.py#L225).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "liveness_health_live_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'status': 'alive', 'uptime_seconds': int(time.time() - _START_TIME), 'timestamp': datetime.now(timezone.utc).isoformat()}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/health/ready`

Readiness probe — are critical dependencies up?

Kubernetes readiness probe.
Checks PostgreSQL, MongoDB, and Redis connectivity concurrently.
Returns 503 if any of those is unavailable so the load balancer stops sending traffic.

Source: [backend/app/routers/health.py:239](../../../backend/app/routers/health.py#L239).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "readiness_health_ready_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
JSONResponse(status_code=200 if critical_ok else 503, content={'status': 'ready' if critical_ok else 'not_ready', 'checks': {'postgres': pg, 'mongo': mongo, 'redis': redis}, 'timestamp': datetime.now(timezone.utc).isoformat()})
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.

## GET `/health/version`

Build identity — version, revision, build date (no probes)

Cheap build-identity report — the running image's version and provenance
with **no** dependency probes, so it answers in microseconds.

``/health/details`` also carries the ``build`` block, but it probes six
services (Postgres, Mongo, Redis, MinIO, Ollama, ChromaDB) and is
documented as "not intended for K8s probes (too slow)". A post-deploy CD
smoke test, an uptime monitor, or an operator confirming a rollout landed
all want a single fast answer to "which commit + build is this pod?" —
that is what this endpoint is for. Root ``GET /`` returns the version but
not the git revision or build date, so it cannot verify a specific build.

Always returns 200 (the process is alive if it can answer at all).
``build.revision`` / ``build.built_at`` fall back to ``"unknown"`` in
local/dev runs where the image-build env is unset.

Source: [backend/app/routers/health.py:262](../../../backend/app/routers/health.py#L262).

Dependency chain: .

Declared Python handler arguments (includes exact role/guard options):

```python

```

### Declared wire contract

References such as `#/components/schemas/...` resolve in [schemas](../schemas.md) or [OpenAPI JSON](../openapi.json).

```json
{
  "operationId": "version_health_version_get",
  "responses": {
    "200": {
      "content": {
        "application/json": {
          "schema": {}
        }
      },
      "description": "Successful Response"
    }
  }
}
```

Handler return expressions (source excerpts, not an inferred wire schema):

```python
{'status': 'ok', 'service': settings.APP_NAME, 'version': settings.APP_VERSION, 'build': build_provenance(), 'env': settings.APP_ENV, 'uptime_seconds': int(time.time() - _START_TIME), 'timestamp': datetime.now(timezone.utc).isoformat()}
```

Contract limit: at least one response has an unstructured schema. Read the linked handler/serializer for emitted fields; the empty schema is not a promise of an empty JSON object.
