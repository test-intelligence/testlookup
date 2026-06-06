#!/usr/bin/env bash
###############################################################################
# validate.sh — offline quality gate for the AWS deployment.
#
# Runs WITHOUT any AWS account or credentials. Every check is static analysis,
# a dry-run, or a render — nothing is provisioned. This is the "validate the
# deployment works without real AWS" gate:
#
#   1. terraform fmt   -check         formatting
#   2. terraform init  -backend=false provider resolution (no state, no AWS)
#   3. terraform validate             HCL + provider-schema correctness
#   4. tflint                         lint / best-practice rules
#   5. tfsec | checkov                IaC security scan
#   6. kustomize build (aws-eks)      the k8s overlay renders
#   7. hadolint                       Dockerfile lint (backend/frontend/mcp)
#   8. shellcheck                     these scripts
#
# Each tool is used from PATH when present, else from its official Docker image,
# else SKIPPED with a notice (CI runs the full set on Linux runners). Exit code
# is non-zero if any executed check fails.
#
# Flags:
#   --tf-docker   Force terraform fmt/validate to run inside the hashicorp/
#                 terraform image. Needed on dev hosts where a TLS-inspecting
#                 AV (e.g. Norton) intercepts loopback and breaks terraform's
#                 plugin mTLS handshake. CI Linux runners don't need this.
#   --no-docker   Never fall back to Docker; skip tools that aren't on PATH.
###############################################################################
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$AWS_DIR/terraform"
REPO_ROOT="$(cd "$AWS_DIR/../.." && pwd)"
EKS_OVERLAY="$REPO_ROOT/k8s/overlays/aws-eks"

TF_IMAGE="hashicorp/terraform:1.10"
FORCE_TF_DOCKER=false
USE_DOCKER=true
for a in "$@"; do
  case "$a" in
    --tf-docker) FORCE_TF_DOCKER=true ;;
    --no-docker) USE_DOCKER=false ;;
    *) echo "unknown flag: $a"; exit 2 ;;
  esac
done

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YEL=$'\033[1;33m'; CYA=$'\033[0;36m'; NC=$'\033[0m'
PASS=0; FAIL=0; SKIP=0
declare -a RESULTS
ok()      { echo "${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); RESULTS+=("PASS  $1"); }
bad()     { echo "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL + 1)); RESULTS+=("FAIL  $1"); }
skip()    { echo "${YEL}[SKIP]${NC} $1"; SKIP=$((SKIP + 1)); RESULTS+=("SKIP  $1"); }
section() { echo; echo "${CYA}── $1 ──────────────────────────────────────────${NC}"; }
# report <exit-code> <label> — record pass/fail without the A && B || C trap.
report()  { if [ "$1" -eq 0 ]; then ok "$2"; else bad "$2"; fi; }

have()      { command -v "$1" >/dev/null 2>&1; }
docker_ok() { [ "$USE_DOCKER" = true ] && have docker && docker info >/dev/null 2>&1; }
# winpath: convert /c/... → C:/... so `docker -v` mounts resolve on Windows.
winpath()   { case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) echo "$1" | sed -E 's|^/([a-zA-Z])/|\U\1:/|' ;; *) echo "$1" ;; esac; }

###############################################################################
section "1-3. Terraform fmt / init / validate"
run_tf_native() {
  (
    cd "$TF_DIR" &&
      terraform fmt -check -recursive &&
      terraform init -backend=false -input=false >/dev/null &&
      terraform validate
  ) 2>&1
}
run_tf_docker() {
  local tmp norton rc
  tmp="$(mktemp -d)"
  cp -r "$TF_DIR/." "$tmp/" && rm -rf "$tmp/.terraform" "$tmp/.terraform.lock.hcl"
  norton="$AWS_DIR/../../backend/certs/mitm-ca.crt"
  local caenv=()
  [ -f "$norton" ] && caenv=(-v "$(winpath "$norton")":/norton.crt:ro -e SSL_CERT_FILE=/norton.crt)
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(winpath "$tmp")":/work -w /work "${caenv[@]}" \
    --entrypoint sh "$TF_IMAGE" \
    -c "terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate" 2>&1
  rc=$?
  rm -rf "$tmp"
  return $rc
}
if have terraform && [ "$FORCE_TF_DOCKER" = false ]; then
  if out="$(run_tf_native)"; then
    ok "terraform fmt + validate (native)"
  elif echo "$out" | grep -qiE "plugin did not respond|failed to load plugin schemas|handshake failed"; then
    echo "${YEL}native plugin mTLS blocked (TLS-inspecting AV on loopback) — retrying in Docker${NC}"
    if docker_ok; then
      out2="$(run_tf_docker)"
      report $? "terraform fmt + validate (docker fallback)"
      [ "$FAIL" -gt 0 ] && echo "$out2" | tail -20
    else
      bad "terraform validate (native blocked, docker unavailable)"
    fi
  else
    bad "terraform validate"
    echo "$out" | tail -20
  fi
elif docker_ok; then
  out="$(run_tf_docker)"
  report $? "terraform fmt + validate (docker)"
  [ "$FAIL" -gt 0 ] && echo "$out" | tail -20
else
  skip "terraform (no terraform binary and no docker)"
fi

###############################################################################
section "4. tflint"
if have tflint; then
  (cd "$TF_DIR" && tflint --recursive)
  report $? "tflint"
elif docker_ok; then
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(winpath "$TF_DIR")":/data -w /data \
    ghcr.io/terraform-linters/tflint --recursive
  report $? "tflint (docker)"
else
  skip "tflint (not installed / no docker)"
fi

###############################################################################
section "5. IaC security scan (tfsec → checkov)"
if have tfsec; then
  (cd "$TF_DIR" && tfsec . --soft-fail)
  report $? "tfsec"
elif have checkov; then
  checkov -d "$TF_DIR" --quiet --compact
  report $? "checkov"
elif docker_ok; then
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(winpath "$TF_DIR")":/src aquasec/tfsec /src --soft-fail
  report $? "tfsec (docker)"
else
  skip "IaC security scan (install tfsec or checkov)"
fi

###############################################################################
section "6. Kustomize render — k8s/overlays/aws-eks"
if [ ! -d "$EKS_OVERLAY" ]; then
  skip "aws-eks overlay not found"
elif have kustomize; then
  kustomize build "$EKS_OVERLAY" >/dev/null
  report $? "kustomize build aws-eks"
elif have kubectl; then
  kubectl kustomize "$EKS_OVERLAY" >/dev/null
  report $? "kubectl kustomize aws-eks"
elif docker_ok; then
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(winpath "$REPO_ROOT")":/repo -w /repo \
    registry.k8s.io/kustomize/kustomize:v5.4.3 build k8s/overlays/aws-eks >/dev/null
  report $? "kustomize build aws-eks (docker)"
else
  skip "kustomize (install kustomize/kubectl)"
fi

###############################################################################
section "7. hadolint — Dockerfiles"
hadolint_one() {
  local df="$REPO_ROOT/$1"
  if have hadolint; then
    hadolint --failure-threshold error "$df"
  elif docker_ok; then
    MSYS_NO_PATHCONV=1 docker run --rm -i hadolint/hadolint \
      hadolint --failure-threshold error - <"$df"
  else
    return 99
  fi
}
if have hadolint || docker_ok; then
  df_fail=0
  for df in backend/Dockerfile frontend/Dockerfile mcp/Dockerfile; do
    if hadolint_one "$df"; then echo "  ok: $df"; else
      echo "  issues: $df"
      df_fail=1
    fi
  done
  report "$df_fail" "hadolint (3 Dockerfiles)"
else
  skip "hadolint (not installed / no docker)"
fi

###############################################################################
section "8. shellcheck — deployment scripts"
sh_files=()
for f in "$SCRIPT_DIR"/*.sh; do sh_files+=("$(basename "$f")"); done
if have shellcheck; then
  (cd "$SCRIPT_DIR" && shellcheck "${sh_files[@]}")
  report $? "shellcheck"
elif docker_ok; then
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(winpath "$SCRIPT_DIR")":/mnt -w /mnt \
    koalaman/shellcheck:stable "${sh_files[@]}"
  report $? "shellcheck (docker)"
else
  skip "shellcheck (not installed / no docker)"
fi

###############################################################################
echo
echo "${CYA}═══════════════ SUMMARY ═══════════════${NC}"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "${CYA}───────────────────────────────────────${NC}"
echo "  ${GREEN}PASS=$PASS${NC}  ${RED}FAIL=$FAIL${NC}  ${YEL}SKIP=$SKIP${NC}"
if [ "$FAIL" -ne 0 ]; then
  echo "${RED}Quality gate FAILED${NC}"
  exit 1
fi
echo "${GREEN}Quality gate PASSED${NC}"
