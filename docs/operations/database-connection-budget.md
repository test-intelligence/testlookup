# PostgreSQL fleet connection budget

TestLookup allocates PostgreSQL pools per operating-system process. Every
Gunicorn worker and every Celery prefork child can therefore consume
`PG_POOL_SIZE + PG_MAX_OVERFLOW` connections independently.

The shipped production contract is:

- PostgreSQL `max_connections`: 400
- operational reserve: 50 connections
- PostgreSQL superuser reserve: 3 connections (verified from the server)
- PostgreSQL `reserved_connections`: 0 by default (verified from the server)
- migration allowance: 8 connections for a cold scale-out
- API pool per process: 2 pooled + 1 overflow
- worker pool per process: 1 pooled + 1 overflow

At the production HPA ceilings, every DB-owning Deployment is also charged one
rolling-surge pod. The resulting maximum is 272 application connections plus
eight simultaneous migration connections. After the PostgreSQL superuser
reserve and the 50-slot operational reserve, 67 additional slots remain.

Run this gate against the rendered production manifest before every rollout:

```bash
kustomize build k8s/overlays/prod > /tmp/testlookup-prod.yaml
python scripts/validate_db_connection_budget.py --manifest /tmp/testlookup-prod.yaml
python scripts/validate_db_connection_budget.py --compose docker-compose.release.yml
```

The Compose validator automatically loads the project `.env` and then applies
exported shell variables, matching Compose precedence. Both shipped Compose
files also make `backend` depend on the one-shot `db-budget-check` service, so
`docker compose up` refuses to start when concurrency or pool overrides no
longer equal the declared Compose requirement.

Every production API process verifies `SHOW max_connections`,
`SHOW superuser_reserved_connections`, `reserved_connections`, and the login
role's connection limit before it becomes ready. Lower database tiers require
lower HPA maxima/concurrency or a separately validated pooler. Do not raise a
pool, worker concurrency, HPA ceiling, or rollout surge unless the validator
continues to pass and the PostgreSQL saturation test shows acceptable checkout
latency under the intended workload.

The PostgreSQL integration suite uses two independent application pools to hold
all six connections allowed by a constrained application role, verifies that a
seventh application connection is rejected, proves separate operational and
migration roles remain available, and verifies recovery after one checkout is
released. The release-scale exercise must additionally scale
all HPAs to their maxima during a rolling restart and sample `pg_stat_activity`
using the `testlookup-api`, `testlookup-worker`, and `testlookup-operation`
application names.
