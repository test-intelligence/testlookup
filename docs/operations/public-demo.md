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
2026 increases. The console shows them in US dollars without VAT (CX23 $6.49,
CX33 $9.99, a public IPv4 address $0.60).

The cost-optimised CX line sells out for days at a time. The Regular
Performance **CPX22** (2 AMD vCPU, 4 GB, 80 GB) runs the demo too, at $19.49
plus $0.60 for IPv4, $3.50 more in Germany. Pull the images there rather than
build them (step 3). A 1 GB type is too small. To move to a CX server later,
create it from step 1, run step 3, store the same tunnel token, then delete
the old server.

## 1. Create the VM

In the Hetzner Cloud console:

1. **Create server.** Location: any EU site (20 TB traffic included; Helsinki
   has no CX types). Image: **Ubuntu 24.04** (the console preselects a newer
   release; the cloud config is written for 24.04). Type: **CX33** (or CX23,
   or CPX22 when CX is sold out).
2. **SSH key:** yours.
3. **Firewall:** create one with a single inbound rule, TCP 22, from your own
   IP address only. The demo needs no other inbound port.
4. **Cloud config:** paste [`deploy/demo/cloud-init.yaml`](../../deploy/demo/cloud-init.yaml).
   It installs Docker, sets the host firewall, unattended upgrades, swap and
   fail2ban, creates the `demo` user and clones this repository to
   `/opt/testlookup`.
5. **Backups:** off. The demo is rebuilt from code and seed data.

## 2. Create the Cloudflare tunnel

In the Cloudflare dashboard (Networking → Tunnels; also under Zero Trust →
Networks → Tunnels):

1. **Create a tunnel** named `testlookup-demo`.
2. The page shows an install command that carries the **tunnel token**. It is
   a secret: it goes only into the VM's `.env` (step 3), never into chat, a
   ticket or the repo. Copy it with the copy icon, in your own browser (an
   embedded or automated browser may not be allowed to write the clipboard).
   **Do not run the command on your computer.** It installs a connector there,
   and Cloudflare then sends part of the demo's traffic to your computer. If
   that happened, the tunnel lists your computer as a second replica; remove
   it with `cloudflared.exe service uninstall` in an Administrator shell.
3. **Route:** Routes → Add route → *Published application*:
   `demo.testlookup.app` → service URL `http://frontend:80`. (cloudflared runs
   inside the Compose network, where the frontend container is called
   `frontend`.) Cloudflare creates the DNS record.

## 3. Start the demo

```bash
ssh root@<vm-address>
sudo -iu demo
cd /opt/testlookup
DEMO_IMAGES=pull scripts/demo/demo.sh setup   # .env, images, seed, start
scripts/demo/demo.sh token                    # paste the tunnel token; starts the tunnel
exit
sudo /opt/testlookup/scripts/demo/demo.sh install-cron   # nightly reset at 03:00, watchdog
```

A VM created before the cloud config ran the host hardening needs
`sudo /opt/testlookup/scripts/demo/harden-host.sh` once (see Security).

`setup` generates a production-mode `.env` with random secrets
(`scripts/gen-dev-env.sh --release`) and binds every published port to
`127.0.0.1`. Docker-published ports bypass host firewalls such as ufw, so
loopback is what keeps them private. It also lowers the worker concurrency for
a small VM and declares the PostgreSQL connection total that goes with it
(`PG_COMPOSE_REQUIRED_CONNECTIONS=19`): the stack's `db-budget-check` starts
nothing unless the two agree.

`DEMO_IMAGES` picks where the app images come from, and `setup` records it in
`.env` for later updates:

- `pull` takes the published images from GHCR (`latest`, which follows main)
  and tags them `testlookup-demo/*:local`. About two minutes. Use it on a 4 GB
  VM: building the frontend needs more memory than such a VM has spare. Set
  `DEMO_PULL_TAG` in `.env` to pin a release. The published images are amd64
  only, so build on an Arm VM.
- `build` (the default) builds the images from this checkout, about ten
  minutes on four vCPU.

`token` reads the token from a hidden prompt, so it never shows on screen or in
the shell history, and stores it in `.env` (mode 600) only. Paste the bare
token or the whole install command; the token is the word that starts with
`eyJ`.

## 4. Cloudflare security rules

All rules are scoped to `demo.testlookup.app`, so the website on the same zone
is untouched. **Bot Fight Mode stays off**: it applies to the whole zone and
cannot be scoped to one host, and the website deliberately allows AI crawlers.
The rules below cover bots on the demo instead.

Security → WAF → **Custom rules**:

| # | Name | Expression | Action |
|---|---|---|---|
| 1 | Demo: block writes | `(http.host eq "demo.testlookup.app" and not http.request.method in {"GET" "HEAD" "OPTIONS"} and not http.request.uri.path in {"/api/v1/auth/demo-login" "/api/v1/auth/refresh" "/api/v1/auth/logout" "/api/v1/release-gate-policies/simulate"} and not (starts_with(http.request.uri.path, "/api/v1/onboarding/") and ends_with(http.request.uri.path, "/detect")))` | Block, custom JSON, 403 (below) |
| 2 | Demo: block internal paths | `(http.host eq "demo.testlookup.app" and (http.request.uri.path eq "/metrics" or starts_with(http.request.uri.path, "/flower") or starts_with(http.request.uri.path, "/mcp") or starts_with(http.request.uri.path, "/sse") or starts_with(http.request.uri.path, "/webhooks/")))` | Block |
| 3 | Demo: browsers only | `(http.host eq "demo.testlookup.app" and not cf.client.bot)` | Managed Challenge, **created disabled** |

Rule 1 mirrors the app's allowlist (`DEMO_WRITE_ALLOWLIST` and
`DEMO_WRITE_PATTERNS`). Change both together. The onboarding exception
exists because the onboarding page reads its progress through
`POST /api/v1/onboarding/{project}/detect`. That call takes no body and
recomputes progress from stored data.

Rule 1's response type is **Custom JSON**, status 403, with the body the app
itself sends:

```json
{"detail":{"code":"demo_read_only","message":"This is a read-only demo, so changes are turned off. Install TestLookup to try everything."}}
```

The app recognises that body and shows its read-only message. Cloudflare's
default block page is HTML, and the app then shows "Request failed with
status code 403" on every refused save.

Rule 3 makes a browser pass a check before any request reaches the demo, and
the check also applies to the app's own API calls. Those are background
requests that cannot complete a check, so each one fails with 403
(`cf-mitigated: challenge`) unless the browser already holds a clearance
cookie. On the live demo that meant a tab opened before the rule existed, or
open past the clearance period, failed every API call, and an embedded browser
stayed on "Just a moment…". Keep it disabled. Turn it on only during an
attack on the demo (see Incidents), and raise **Security → Settings →
Challenge Passage** to a few hours while it is on. The rate limits, the app's
own limits and the nightly reset cover the demo without it.

Security → WAF → **Rate limiting rules** (the Pro plan allows two):

| # | Name | Matches | Limit (per IP) | Action |
|---|---|---|---|---|
| A | Demo: API | `(http.host eq "demo.testlookup.app" and starts_with(http.request.uri.path, "/api/"))` | 60 requests per 10 s | Block (429) for 1 minute |
| B | Demo: sign-in | `(http.host eq "demo.testlookup.app" and http.request.uri.path eq "/api/v1/auth/demo-login")` | 5 requests per 10 s | Block (429) for 1 minute |

A dashboard visit made 21 API calls in its busiest 10 seconds, so rule A
leaves about three times that. Watch Security → Events for a few days and
adjust it. Behind the edge, nginx allows
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
  `curl -i -X POST https://demo.testlookup.app/api/v1/projects` answers 403
  from `Server: cloudflare` with the `demo_read_only` JSON. Security → Events
  shows rule 1.
- A GET such as `curl -i https://demo.testlookup.app/api/v1/auth/options`
  answers 200 with `"demo_mode":true`. A 403 with `cf-mitigated: challenge`
  means rule 3 is on.
- `scripts/demo/demo.sh status` on the VM: every container is up and healthy,
  with memory well under its ceiling.
- No container but cloudflared reaches the internet: on the VM,
  `docker exec testlookup_backend python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)"`
  fails with a name-resolution error.
- `sudo WATCHDOG_DRY_RUN=1 /opt/testlookup/scripts/demo/watchdog.sh` prints
  nothing.
- The next morning, `/var/log/testlookup-demo-reset.log` ends with
  `reset: done`.
- Optional: set `DEMO_HEARTBEAT_URL` in `.env` to a free heartbeat monitor's
  ping URL. Each successful reset pings it, so a failed night alerts you.
  Point a free uptime monitor at `https://demo.testlookup.app/api/v1/auth/options`.

## Security

The demo is public, so it is built on the assumption that someone will try to
break in, mine cryptocurrency on it, or run up the bill. Every threat below has
at least two independent controls.

| Threat | What stops it |
|---|---|
| Changing or deleting data | Cloudflare rule 1; `DEMO_MODE` in the app; the nightly rebuild from seed |
| Bots, scrapers, floods | Cloudflare DDoS mitigation and rate limits A and B; nginx's per-address limit; the app's sign-in limit. Rule 3 and Under Attack mode are held in reserve |
| A bill spike | A fixed-price VM; nothing billed per request or per GB; no LLM key, AI offline |
| A crypto miner, through a bug in the app | The app's containers run as a non-root user with no capabilities and no-new-privileges. **No container but cloudflared has a route out** (`testlookup_net` is internal), so a miner can neither be downloaded nor reach a pool. CPU and process ceilings on every container. The watchdog kills miners and rebuilds the containers. Every container is rebuilt from its image every night |
| Escaping a container | No Docker socket in any container, nothing privileged, AppArmor and seccomp defaults, automatic security updates for the kernel and Docker CE, reboot at 04:30 when a kernel needs it |
| Guessing or stealing SSH access | Keys only, root only, passwords locked. The Hetzner Cloud Firewall admits SSH from the administrator's address only. ufw rate-limits SSH; fail2ban bans after 3 failures and bans repeat offenders for a week |
| An exposed database or admin port | Docker publishes no port from an internal network; published ports are also bound to `127.0.0.1`; ufw denies inbound; the Hetzner firewall allows port 22 only |
| A stolen tunnel token | The token lives only in the VM's `.env` (mode 600). Rotate it in Cloudflare (tunnel → Refresh token), then `demo.sh token` |
| A tampered image or repository | Images come from the project's own GHCR. Pin `DEMO_PULL_TAG` to a release once releases exist |
| A taken-over account | Owner: two-factor authentication on Hetzner, Cloudflare and GitHub |

### Host hardening

`deploy/demo/cloud-init.yaml` runs [`scripts/demo/harden-host.sh`](../../scripts/demo/harden-host.sh)
on a new VM. On an existing VM, run it once (it is idempotent):

```bash
sudo /opt/testlookup/scripts/demo/harden-host.sh
```

It sets SSH to keys only with no forwarding, rate-limits SSH in ufw, adds the
fail2ban jails, kernel settings (no redirects or source routing, kernel
pointers hidden), Docker defaults (`no-new-privileges`, `live-restore`, log
rotation) and Docker CE security updates. It restarts Docker once, so the demo
restarts with it.

SSH is reachable only from the address in the Hetzner firewall rule. If your
address changes, edit the rule in the Hetzner console (Firewalls →
`testlookup-demo-ssh-only`). If the SSH key is lost, use Hetzner's rescue
system; passwords are locked and the web console cannot log in.

### Container lockdown

`docker-compose.demo.yml` makes `testlookup_net` internal and gives
cloudflared alone a second network, `demo_egress`, to dial Cloudflare. The
one-shot `db-budget-check` runs with no network. Every container gets
`no-new-privileges` and ceilings on memory, CPU (at most 1.5 vCPU each) and
process count. The containers that run as a non-root user from the start (the
backend image's services and cloudflared) drop every Linux capability.

### Watchdog

`sudo scripts/demo/demo.sh install-cron` also runs
[`scripts/demo/watchdog.sh`](../../scripts/demo/watchdog.sh) as root every 5
minutes. It:

- kills miner processes (xmrig, kinsing, stratum URLs and the like); a miner
  inside a container also triggers a rebuild of every container
  (`demo.sh reset`);
- resets the demo when the host has been at least 85 % busy for 15 minutes;
- reports a container other than cloudflared that has a route out, an
  unexpected listening port, and connections to mining-pool ports (it kills
  the process behind a pool connection).

It resets the demo at most once an hour, and repeats an alert at most once an
hour. Alerts go to `/var/log/testlookup-demo-watchdog.log`. To get them on
your phone, set `DEMO_ALERT_URL` in `.env`: an [ntfy.sh](https://ntfy.sh)
topic URL with a long random topic name (subscribe to it in the ntfy app), or
a healthchecks.io "/fail" URL. Try it with
`sudo WATCHDOG_DRY_RUN=1 /opt/testlookup/scripts/demo/watchdog.sh`, which
reports without acting.

### If something got in

1. Remove the tunnel's route in Cloudflare. The demo goes dark at once.
2. Refresh the tunnel token in Cloudflare, so a copy taken from the VM is
   useless.
3. Delete the VM in Hetzner. Take a snapshot first only if you want to
   investigate; snapshots are billed.
4. Rebuild from step 1. Use a new SSH key if the old one may have leaked, and
   store the new tunnel token with `demo.sh token`.

There is nothing to recover: the data is sample data, and the `.env` secrets
are random per VM and shared with nothing else.

### Owner checklist

- Two-factor authentication on the Hetzner, Cloudflare and GitHub accounts.
- No Hetzner API tokens in the demo project; Cloudflare API tokens scoped to
  what they deploy.
- The demo's SSH private key stays on the administrator's machine. Consider a
  passphrase (`ssh-keygen -p -f ~/.ssh/<key>`) with an SSH agent.

### Not done, on purpose

- **User-namespace remapping:** it changes volume ownership for the data-store
  images, and the app images already run unprivileged.
- **Read-only root filesystems:** the app writes caches. With no route out,
  little is gained.
- **Image signature checks:** add a `cosign verify` before tagging once release
  images are signed.
- **An intrusion-detection system:** the watchdog and the nightly rebuild cover
  the realistic threat to a disposable demo.

## Incidents

Fastest first:

1. Enable rule 3 (Demo: browsers only) and raise Challenge Passage. Only the
   demo host is challenged; open tabs need a reload. Disable it afterwards.
2. Cloudflare → Overview → **Under Attack mode**. Every visitor gets a
   challenge, on the whole zone, website included. Turn it off afterwards.
3. Tighten rate-limit rule A, or add a block rule for an abusive country or
   ASN.
4. Remove the tunnel's route. The demo goes dark at the edge and the VM
   receives nothing.
5. `docker compose ... down` on the VM, or delete and recreate the VM from
   step 1. There is no data to recover and no secret shared with anything
   else.

## Routine work

| When | What |
|---|---|
| Nightly, automatic | `demo.sh reset` (cron, 03:00): wipe and re-seed, about a minute offline. An open demo tab shows "The live demo is refreshing" meanwhile, and its visitor signs in again afterwards (the demo account is re-created) |
| Automatic | OS security updates; reboots at 04:30 when a kernel needs it |
| Each release | `scripts/demo/demo.sh update`: pull the code, new images (`DEMO_IMAGES`), reset |
| Automatic, every 5 minutes | The watchdog (miners, sustained CPU, egress drift, unexpected ports) |
| Monthly, 5 minutes | Glance at Cloudflare Security → Events, the reset and watchdog logs, and `sudo fail2ban-client status sshd` |

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
