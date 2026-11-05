"""Production API process lifecycle hooks."""

from prometheus_client import multiprocess

bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"

# Proxy trust is NOT configured here. See app/bootstrap.py.
#
# Re-audit finding H2 needs the API to trust its ingress's X-Forwarded-For, and
# the only useful way to express that is a CIDR -- the proxy is a pod whose
# address changes. gunicorn 23 could not express it: ForwardedAllowIPS
# validated with ipaddress.ip_address(), which raises on any network, and it
# validates the $FORWARDED_ALLOW_IPS default while BUILDING its Config, before
# this file is read. So a ConfigMap carrying a CIDR killed every worker at
# startup with "does not appear to be an IPv4 or IPv6 address", and nothing
# written here could have caught it.
#
# gunicorn 24.1 accepts CIDRs, but with strict ipaddress.ip_network(), so a
# value with host bits set ("10.42.0.5/16") still kills startup the same way,
# and docker-compose.gcp-vm.yml runs a bare uvicorn that never reads this file.
# The boundary therefore stays inside the ASGI app, using the same uvicorn
# ProxyHeadersMiddleware gunicorn's setting would have configured, read from
# TRUSTED_PROXY_IPS -- a name gunicorn does not touch. Leaving
# forwarded_allow_ips unset keeps gunicorn's own default of loopback-only,
# which makes uvicorn's built-in copy of that middleware a no-op for proxied
# traffic and leaves ours the single place the decision is made.


def child_exit(_server, worker) -> None:
    """Remove live-gauge files for every reaped API worker, including crashes."""
    multiprocess.mark_process_dead(worker.pid)
