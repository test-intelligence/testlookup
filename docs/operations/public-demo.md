# Public read-only demo

How the public demo at `demo.testlookup.app` is built and run. It lets anyone
explore TestLookup with sample data, without installing anything and without
being able to change anything.

The design has three goals:

- **A fixed bill.** Nothing is billed per request or per GB. An attack can
  make the demo slow or unavailable, but it cannot raise the bill.
- **Refuse, don't scale.** Floods are blocked at the Cloudflare edge, abusive
  addresses are rate-limited, and there is no autoscaling to pay for.
- **Little to operate.** One VM, one script and one nightly cron job. The demo
  holds no real data, so a broken VM is rebuilt rather than repaired.

## Architecture

```
visitor ──HTTPS──► Cloudflare (testlookup.app zone)
                     • DDoS mitigation, caching of hashed SPA assets
                     • WAF: block writes and internal paths on the demo host
                     • rate limits per IP
                         │  outbound-only tunnel: no inbound port on the VM
                         ▼
             VM (Hetzner CX33, Ubuntu 24.04) — firewall: SSH only
               docker compose -f docker-compose.release.yml -f docker-compose.demo.yml
               cloudflared ─► frontend (nginx: real IP + limit_req) ─► backend (DEMO_MODE)
                                                                  ├─ postgres, mongo, valkey, S3 store
                                                                  └─ worker, worker-children, beat
               cron 03:00 ─► scripts/demo/demo.sh reset (wipe volumes, re-seed)
```

Writes are blocked three times:

1. **At the edge**, by a Cloudflare custom rule. Writes never reach the VM.
2. **In the app**, by `DEMO_MODE=true` (`backend/app/core/demo_mode.py`).
   Every request that is not GET, HEAD or OPTIONS gets 403 `demo_read_only`
   before routing, except demo sign-in, token refresh, logout, the
   release-gate policy simulation and the onboarding progress refresh.
   Password login is refused too. Visitors
   sign in with one click (`POST /api/v1/auth/demo-login`) as `DEMO_USERNAME`,
   which must not be an admin, a service account or an MFA account. The
   setting is environment-only, so it cannot be switched off through the API.
3. **In the data.** The nightly reset deletes every volume and re-seeds. The
   30-day sample history always ends today, and nothing a visitor did
   survives the night.

Nothing on the demo can reach outside. `docker-compose.demo.yml` forces:

- offline AI, with every LLM key empty;
- SMTP off;
- self-registration and dev-login off;
- no MCP server.

**Never put a paid LLM key on the demo.** Bots would run up the bill through
the AI features; it is the one cost-spike route in this app.

| Item | Monthly cost |
|---|---|
| Hetzner CX33 (4 vCPU, 8 GB, 80 GB, 20 TB traffic, IPv4), excl. VAT | €8.99 (CX23, 4 GB: €5.99) |
| Cloudflare (the existing `testlookup.app` zone) and the tunnel | no extra cost |
| Domain | already owned |

Prices are from Hetzner's pages in November 2026, after its April and June
2026 increases.

## 1. Create the VM

In the Hetzner Cloud console:

1. **Create server.** Location: any EU site (20 TB traffic included).
   Image: Ubuntu 24.04. Type: **CX33** (or CX23).
2. **SSH key:** yours.
3. **Firewall:** create one with a single inbound rule, TCP 22, from your own
   IP address only. The demo needs no other inbound port.
4. **Cloud config:** paste [`deploy/demo/cloud-init.yaml`](../../deploy/demo/cloud-init.yaml).
   It installs Docker, sets the host firewall, unattended upgrades, swap and
   fail2ban, creates the `demo` user and clones this repository to
   `/opt/testlookup`.
5. **Backups:** off. The demo is rebuilt from code and seed data.

## 2. Create the Cloudflare tunnel

In the Cloudflare dashboard (Zero Trust → Networks → Tunnels):

1. **Create a tunnel**, type *Cloudflared*, named `testlookup-demo`.
2. Copy the **tunnel token** from the install command. It is a secret: it goes
   only into the VM's `.env` (step 3), never into chat, a ticket or the repo.
3. **Public hostname:** `demo.testlookup.app` → service `HTTP` →
   `frontend:80`. (cloudflared runs inside the Compose network, where the
   frontend container is called `frontend`.)

## 3. Start the demo

```bash
ssh root@<vm-address>
sudo -iu demo
cd /opt/testlookup
scripts/demo/demo.sh setup        # .env, builds the images (~10 min), seeds, starts
nano .env                         # set CLOUDFLARE_TUNNEL_TOKEN=<token from step 2>
scripts/demo/demo.sh reset        # restarts with the tunnel
exit
sudo /opt/testlookup/scripts/demo/demo.sh install-cron   # nightly reset at 03:00
```

`setup` generates a production-mode `.env` with random secrets
(`scripts/gen-dev-env.sh --release`) and binds every published port to
`127.0.0.1`. Docker-published ports bypass host firewalls such as ufw, so
loopback is what keeps them private. It also builds the images from this
checkout, tagged `testlookup-demo/*:local`.

Once release images are public, set `TESTLOOKUP_IMAGE=ghcr.io/test-intelligence/testlookup`
and a release tag in `TESTLOOKUP_VERSION`, and skip `build`.

## 4. Cloudflare security rules

All rules are scoped to `demo.testlookup.app`, so the website on the same zone
is untouched. **Bot Fight Mode stays off**: it applies to the whole zone and
cannot be scoped to one host, and the website deliberately allows AI crawlers.
The rules below cover bots on the demo instead.

Security → WAF → **Custom rules**:

| # | Name | Expression | Action |
|---|---|---|---|
| 1 | Demo: block writes | `(http.host eq "demo.testlookup.app" and not http.request.method in {"GET" "HEAD" "OPTIONS"} and not http.request.uri.path in {"/api/v1/auth/demo-login" "/api/v1/auth/refresh" "/api/v1/auth/logout" "/api/v1/release-gate-policies/simulate"} and not (starts_with(http.request.uri.path, "/api/v1/onboarding/") and ends_with(http.request.uri.path, "/detect")))` | Block |
| 2 | Demo: block internal paths | `(http.host eq "demo.testlookup.app" and (http.request.uri.path eq "/metrics" or starts_with(http.request.uri.path, "/flower") or starts_with(http.request.uri.path, "/mcp") or starts_with(http.request.uri.path, "/sse") or starts_with(http.request.uri.path, "/webhooks/")))` | Block |
| 3 | Demo: browsers only | `(http.host eq "demo.testlookup.app" and not cf.client.bot)` | Managed Challenge |

Rule 1 mirrors the app's allowlist (`DEMO_WRITE_ALLOWLIST` and
`DEMO_WRITE_PATTERNS`). Change both together. The onboarding exception
exists because the onboarding page reads its progress through
`POST /api/v1/onboarding/{project}/detect`. That call takes no body and
recomputes progress from stored data.

Rule 3 makes a visitor's browser pass a mostly invisible check once. The
browser then carries a clearance cookie, so the app's API calls pass without
another check. Clients that cannot run JavaScript are stopped at the edge. It
has one cost: when the clearance expires, an open tab's API calls fail until
the page is reloaded. Raise **Security → Settings → Challenge Passage** to a
few hours. Start with rule 3 on. If real visitors report trouble, switch it
off and rely on the rate limits.

Security → WAF → **Rate limiting rules** (the Pro plan allows two):

| # | Name | Matches | Limit (per IP) | Action |
|---|---|---|---|---|
| A | Demo: API | `(http.host eq "demo.testlookup.app" and starts_with(http.request.uri.path, "/api/"))` | 60 requests per 10 s | Block |
| B | Demo: sign-in | `(http.host eq "demo.testlookup.app" and http.request.uri.path eq "/api/v1/auth/demo-login")` | 5 requests per 10 s | Block |

A dashboard load fires a burst of parallel API calls. Watch Security →
Events for a few days and adjust rule A. Behind the edge, nginx allows
20 requests/s per address with a burst of 80
(`deploy/demo/nginx-demo-edge.conf`), and the API allows 10 demo sign-ins per
minute per address.

No cache rule is needed. nginx marks hashed assets `public, immutable` (so
Cloudflare caches them) and `index.html` `no-cache`. API responses are not
cached.

## 5. Verify

- `https://demo.testlookup.app` shows **Explore the TestLookup demo** with one
  button, and no password form. One click lands in the app with the
  **Live demo · read-only** banner.
- Saving anything shows "This is a read-only demo…".
- From any machine, a write is refused at the edge before it reaches the VM:
  `curl -i -X POST https://demo.testlookup.app/api/v1/projects` answers
  Cloudflare's 403. Security → Events shows rule 1.
- `scripts/demo/demo.sh status` on the VM: every container is up, with memory
  well under its ceiling.
- The next morning, `/var/log/testlookup-demo-reset.log` ends with
  `reset: done`.
- Optional: set `DEMO_HEARTBEAT_URL` in `.env` to a free heartbeat monitor's
  ping URL. Each successful reset pings it, so a failed night alerts you.
  Point a free uptime monitor at `https://demo.testlookup.app/api/v1/auth/options`.

## Incidents

Fastest first:

1. Cloudflare → Overview → **Under Attack mode**. Every visitor gets a
   challenge, on the whole zone, website included. Turn it off afterwards.
2. Tighten rate-limit rule A, or add a block rule for an abusive country or
   ASN.
3. Remove the tunnel's public hostname. The demo goes dark at the edge and
   the VM receives nothing.
4. `docker compose ... down` on the VM, or delete and recreate the VM from
   step 1. There is no data to recover and no secret shared with anything
   else.

## Routine work

| When | What |
|---|---|
| Nightly, automatic | `demo.sh reset` (cron, 03:00): wipe and re-seed, about a minute offline |
| Automatic | OS security updates; reboots at 04:30 when a kernel needs it |
| Each release | `scripts/demo/demo.sh update`: pull, rebuild, reset |
| Monthly, 5 minutes | Glance at Cloudflare Security → Events and the reset log |

To retire the demo, delete the tunnel and the VM. Nothing else depends on
them.

## Related

- [Compose deployments](deploy-compose.md): the release stack the demo builds
  on.
- [Configuration reference](configuration.md): `DEMO_MODE` and
  `DEMO_USERNAME`.
- For a look without any server, the website's
  [guided tours](https://testlookup.app/demo/first-run/) replay real screens.
  Anyone can also run the full app in their own GitHub Codespace
  ([`.devcontainer/README.md`](../../.devcontainer/README.md)).
