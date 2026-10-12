#!/usr/bin/env bash
# lib-site-config.sh — shared by deploy-homelab.sh and bootstrap-homelab.sh.
#
# Site-specific values (host name, registry, node addresses, SSH user, extra
# kustomize patches) live in a local env file that is NOT part of the
# repository. TESTLOOKUP_HOMELAB_ENV names it; without that variable the
# per-user default ${XDG_CONFIG_HOME:-$HOME/.config}/testlookup/homelab.env is
# read when it exists. homelabsetup/homelab.env.example documents every key.

# Reads KEY=VALUE lines (comments, blank lines and an optional `export ` are
# fine; one level of matching quotes is stripped). It never executes the file,
# and a variable that is already set in the environment keeps its value.
load_site_config() {
  local file="${TESTLOOKUP_HOMELAB_ENV:-}" line key value
  if [ -z "$file" ]; then
    file="${XDG_CONFIG_HOME:-$HOME/.config}/testlookup/homelab.env"
    [ -f "$file" ] || return 0
  fi
  if [ ! -f "$file" ] && command -v cygpath >/dev/null 2>&1; then
    file="$(cygpath -u "$file")"
  fi
  if [ ! -f "$file" ]; then
    echo "[x] TESTLOOKUP_HOMELAB_ENV points at '$file', which does not exist." >&2
    exit 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    key="${line%%=*}"
    [ "$key" != "$line" ] || continue
    case "$key" in ''|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
    value="${line#*=}"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    if [ -z "${!key+x}" ]; then
      export "$key=$value"
    fi
  done < "$file"
  SITE_CONFIG_FILE="$file"
}

