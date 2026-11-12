# k3ssetup/ — the single-node K3s path

The scripts in this directory install TestLookup on a K3s cluster: one machine
is enough, more nodes join as agents. The full guide is
[docs/operations/deploy-k3s.md](../docs/operations/deploy-k3s.md).

| File | Purpose |
|---|---|
| `deploy-k3s.sh` | Build, push and deploy (first install and every update) |
| `bootstrap-k3s.sh` | Optional: create the K3s cluster over SSH |
| `lib-site-config.sh` | Reads your site configuration (shared by both scripts) |
| `k3s.env.example` | Every site setting, documented — copy it outside the repository |

Quick reference:

```bash
cp k3ssetup/k3s.env.example ~/.config/testlookup/k3s.env   # then edit
./k3ssetup/deploy-k3s.sh                                       # first install
./k3ssetup/deploy-k3s.sh --skip-registry --skip-models         # updates
```

Site-specific values (host name, registry, node addresses, SSH user, site
patches) never live in the repository: the scripts read them from the file
named by `TESTLOOKUP_K3S_ENV` (default
`~/.config/testlookup/k3s.env`). A first install generates every secret and
the initial admin password, shows them once and saves them to
`k3ssetup/.k3s-credentials` (gitignored, mode 600); re-runs never
regenerate a secret or touch an existing admin.
