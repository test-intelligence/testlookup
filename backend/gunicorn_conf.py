"""Production API process lifecycle hooks."""

import os

from prometheus_client import multiprocess

bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"

# Which peer addresses may supply X-Forwarded-For / X-Forwarded-Proto.
#
# Re-audit finding H2. Without this, uvicorn trusts only 127.0.0.1, so behind
# ingress-nginx (or the frontend container's own /api proxy) request.client.host
# is the PROXY address — identical for every external client. Two things broke
# silently on that:
#
#   * the login/MFA rate limiter (app/main.py) buckets per client address, so
#     the whole external user base shared one bucket per worker: one script
#     exhausting it 429s everyone else out of logging in;
#   * every IP recorded for lockout and audit forensics (auth, mfa, scim, sso)
#     recorded that same proxy address, so post-incident triage cannot tell
#     callers apart.
#
# UvicornWorker forwards this gunicorn setting into ProxyHeadersMiddleware.
#
# The default stays 127.0.0.1 — trust nothing — so a deployment that has not
# thought about its proxy topology never silently starts believing a header any
# client can set. Deployments opt in explicitly:
#
#   * Kubernetes: "*" in the ConfigMap. The precondition is the default-deny
#     NetworkPolicy, which admits ingress to :8000 only from ingress-nginx, the
#     frontend, the workers and the MCP server. If that policy is widened,
#     narrow this to the ingress controller pod CIDR in the same change.
#   * Compose: "*" on the private bridge network, where only sibling services
#     (the nginx frontend, the workers) can reach the backend port at all.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")


def child_exit(_server, worker) -> None:
    """Remove live-gauge files for every reaped API worker, including crashes."""
    multiprocess.mark_process_dead(worker.pid)
