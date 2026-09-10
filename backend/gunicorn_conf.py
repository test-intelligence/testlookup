"""Production API process lifecycle hooks."""

import os

from prometheus_client import multiprocess

bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"

# Which peer addresses may supply X-Forwarded-For / X-Forwarded-Proto.
#
# Re-audit finding H2. Without a declared trust boundary uvicorn trusts only
# loopback, so behind ingress-nginx (or the frontend container's own /api
# proxy) request.client.host is the PROXY address for every caller. Two things
# broke silently on that:
#
#   * the login/MFA rate limiter (app/main.py) buckets per client address, so
#     the whole external user base shared one bucket per worker: one script
#     exhausting it 429s everyone else out of logging in;
#   * every IP recorded for lockout and audit forensics (auth, mfa, scim, sso)
#     recorded that same proxy address, so post-incident triage cannot tell
#     callers apart.
#
# NEVER set this to "*". Under a wildcard uvicorn takes the LEFTMOST entry of
# X-Forwarded-For (proxy_headers.get_trusted_client_address), and every proxy
# in this repo APPENDS ($proxy_add_x_forwarded_for in nginx.conf.template).
# The leftmost entry is therefore whatever the caller sent, so a wildcard makes
# request.client.host caller-controlled: the rate limiter above becomes
# defeatable by rotating a header, and the forensic IPs go from useless to
# forgeable. Measured on uvicorn 0.49.0 with "1.2.3.4, 203.0.113.9, 10.42.1.7":
# "*" yields 1.2.3.4 (the spoof); "10.42.0.0/16" yields 203.0.113.9 (correct).
#
# Set it to the address range the PROXY connects from -- nothing wider. With a
# real range uvicorn walks the header in reverse and returns the first hop it
# does not trust, which is the true client, and it ignores the header entirely
# unless the immediate TCP peer is itself trusted. Widening the range to cover
# the clients as well as the proxy re-opens the spoof: on a LAN deployment
# 192.168.0.0/16 makes an on-LAN caller trusted, so the walk runs past its
# address and returns the spoofed leftmost entry again.
#
# gunicorn already reads FORWARDED_ALLOW_IPS on its own, defaulting to
# "127.0.0.1,::1"; this restates it so the value is visible where the
# consequences are documented, and so a regression test can pin it.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")


def child_exit(_server, worker) -> None:
    """Remove live-gauge files for every reaped API worker, including crashes."""
    multiprocess.mark_process_dead(worker.pid)
