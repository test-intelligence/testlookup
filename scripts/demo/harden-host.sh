#!/usr/bin/env bash
# ============================================================================
# Harden the TestLookup public demo VM's host. Idempotent; run as root.
# Runbook: docs/operations/public-demo.md ("Security").
#
#   sudo scripts/demo/harden-host.sh
#
# What it sets, and why:
#   * SSH: keys only, root only, no forwarding of any kind, few tries. The
#     demo needs nothing over SSH but an administrator's shell.
#   * Firewall: SSH rate-limited (ufw limit). The Hetzner Cloud Firewall in
#     front already admits SSH from the administrator's address only.
#   * fail2ban: 3 failures in 10 minutes ban for an hour; repeat offenders
#     for a week.
#   * Kernel: no ICMP redirects or source routing, martians logged, no core
#     dumps from setuid programs, kernel pointers hidden.
#   * Docker: no-new-privileges for every container by default, live-restore
#     (a Docker upgrade or restart no longer stops the demo), log rotation.
#   * Unattended upgrades also install Docker CE security releases (runc and
#     containerd escapes are the realistic way out of a container).
# ============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "harden-host: run as root" >&2; exit 1; }
log() { printf '[harden %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── SSH ──────────────────────────────────────────────────────────────────────
# sshd uses the first value it reads, and Include sorts the drop-ins, so this
# 00- file wins over anything a cloud image adds later (50-cloud-init.conf).
install -d -m 755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-testlookup-demo.conf <<'CONF'
# TestLookup demo VM (scripts/demo/harden-host.sh): keys only, no forwarding.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin prohibit-password
AllowUsers root
X11Forwarding no
AllowTcpForwarding no
AllowAgentForwarding no
AllowStreamLocalForwarding no
PermitTunnel no
GatewayPorts no
MaxAuthTries 4
MaxSessions 4
LoginGraceTime 20
ClientAliveInterval 300
ClientAliveCountMax 2
CONF
chmod 644 /etc/ssh/sshd_config.d/00-testlookup-demo.conf
sshd -t
# Ubuntu 24.04 starts sshd per connection from ssh.socket, so new logins read
# the file at once; reload covers a classic always-running sshd.
systemctl reload ssh.service 2>/dev/null || true
log "ssh: keys only, root only, no forwarding"

# Passwords stay locked: keys are the only way in (the Hetzner web console
# cannot log in either; use its rescue system if the key is ever lost).
for user in root demo; do
  if id "$user" >/dev/null 2>&1 && [ "$(passwd -S "$user" | awk '{print $2}')" = "P" ]; then
    passwd -l "$user" >/dev/null
    log "locked the password of $user"
  fi
done

# ── Firewall ─────────────────────────────────────────────────────────────────
ufw limit OpenSSH >/dev/null
ufw --force enable >/dev/null
log "ufw: inbound deny, SSH rate-limited"

# ── fail2ban ─────────────────────────────────────────────────────────────────
cat >/etc/fail2ban/jail.d/testlookup-demo.local <<'CONF'
# TestLookup demo VM (scripts/demo/harden-host.sh)
[sshd]
enabled  = true
mode     = aggressive
maxretry = 3
findtime = 10m
bantime  = 1h

[recidive]
enabled  = true
maxretry = 3
findtime = 1d
bantime  = 1w
CONF
systemctl restart fail2ban
log "fail2ban: sshd 3 tries / 10 min -> 1 h ban; recidive -> 1 week"

# ── Kernel ───────────────────────────────────────────────────────────────────
cat >/etc/sysctl.d/90-testlookup-demo.conf <<'CONF'
# TestLookup demo VM (scripts/demo/harden-host.sh)
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
fs.suid_dumpable = 0
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
CONF
sysctl --system >/dev/null
log "kernel: redirects and source routing off, kptr hidden"

# ── Docker ───────────────────────────────────────────────────────────────────
desired='{
  "no-new-privileges": true,
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}'
install -d -m 755 /etc/docker
if [ "$(cat /etc/docker/daemon.json 2>/dev/null || true)" != "$desired" ]; then
  printf '%s\n' "$desired" >/etc/docker/daemon.json
  log "docker: daemon.json changed, restarting Docker (the demo restarts with it)"
  systemctl restart docker
else
  log "docker: daemon.json already in place"
fi

# ── Unattended upgrades: Docker CE too ───────────────────────────────────────
cat >/etc/apt/apt.conf.d/53testlookup-demo-docker <<'CONF'
// TestLookup demo VM (scripts/demo/harden-host.sh): Docker CE security fixes.
Unattended-Upgrade::Origins-Pattern:: "origin=Docker,label=Docker CE";
CONF
log "unattended upgrades: Docker CE included"

log "done"
