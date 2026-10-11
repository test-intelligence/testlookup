# homelabsetup/ — the single-node K3s path

The scripts in this directory install TestLookup on a K3s cluster: one machine
is enough, more nodes join as agents. The full guide is
[docs/operations/deploy-k3s.md](../docs/operations/deploy-k3s.md).

| File | Purpose |
|---|---|
| `deploy-homelab.sh` | Build, push and deploy (first install and every update) |
| `bootstrap-homelab.sh` | Optional: create the K3s cluster over SSH |
| `lib-site-config.sh` | Reads your site configuration (shared by both scripts) |
| `homelab.env.example` | Every site setting, documented — copy it outside the repository |

Quick reference:

```bash
cp homelabsetup/homelab.env.example ~/.config/testlookup/homelab.env   # then edit
./homelabsetup/deploy-homelab.sh                                       # first install
./homelabsetup/deploy-homelab.sh --skip-registry --skip-models         # updates
```

Site-specific values (host name, registry, node addresses, SSH user, site
patches) never live in the repository: the scripts read them from the file
named by `TESTLOOKUP_HOMELAB_ENV` (default
`~/.config/testlookup/homelab.env`). A first install generates every secret and
the initial admin password, shows them once and saves them to
`homelabsetup/.homelab-credentials` (gitignored, mode 600); re-runs never
regenerate a secret or touch an existing admin.
