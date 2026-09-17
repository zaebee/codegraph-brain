#!/usr/bin/env bash
# Decide whether server.json's version should be published to the MCP Registry,
# and wait until PyPI can back it (#471).
#
#   scripts/mcp_registry_gate.sh check     → prints publish=true|false (for $GITHUB_OUTPUT)
#   scripts/mcp_registry_gate.sh wait-pypi → blocks until PyPI serves the version
#
# `check` is what makes the workflow safe to run after *every* Release Please run:
# only a tagged release that the registry does not list yet is published, so a
# docs push to main is a no-op and a re-run never double-publishes.
#
# Every lookup fails closed. `publish=false` is printed only on a definite answer
# (no such tag, version already listed); a network error or an unexpected
# response exits non-zero, because a silently skipped release is exactly what
# this automation exists to prevent.
#
# `wait-pypi` exists because the registry verifies ownership by fetching
# pypi.org/pypi/<pkg>/<version>/json and looking for the mcp-name marker, and a
# fresh upload is not always visible there straight away (0.23.0 and 0.23.1 both
# failed a `pip install` for a few minutes after the publish job went green).
set -euo pipefail

REGISTRY="${MCP_REGISTRY_URL:-https://registry.modelcontextprotocol.io}"
PYPI="${PYPI_URL:-https://pypi.org}"
TAG_PREFIX="${TAG_PREFIX:-codegraph-brain-v}"
WAIT_SECONDS="${WAIT_SECONDS:-600}"
POLL_SECONDS="${POLL_SECONDS:-15}"

fail() {
  echo "::error::$*" >&2
  exit 1
}

read_field() {
  local filter="$1" label="$2" value
  value=$(jq -r "${filter} // empty" server.json) || fail "server.json is not valid JSON."
  [[ -n "$value" ]] || fail "server.json has no ${label} (${filter})."
  printf '%s' "$value"
}

name=$(read_field .name "server name")
version=$(read_field .version "version")
package=$(read_field 'first(.packages[]? | select(.registryType == "pypi") | .identifier)' "PyPI package")

check() {
  local tag="refs/tags/${TAG_PREFIX}${version}" rc=0
  git ls-remote --exit-code --tags origin "$tag" >/dev/null || rc=$?
  case "$rc" in
    0) ;;
    2)
      echo "No tag ${TAG_PREFIX}${version}: not a release, nothing to publish." >&2
      echo "publish=false"
      return
      ;;
    *) fail "git ls-remote for ${tag} failed (exit ${rc}); cannot tell whether this is a release." ;;
  esac

  # The status alone is not enough: a moved API also answers 404, and a proxy can
  # answer 200 with nothing. Each answer must also carry the body that means it.
  local encoded body status
  encoded=$(jq -rn --arg n "$name" '$n | @uri')
  body=$(mktemp)
  status=$(curl -sS -o "$body" -w '%{http_code}' \
    "${REGISTRY}/v0/servers/${encoded}/versions/${version}") ||
    fail "Registry lookup for ${name} ${version} failed."
  if [[ "$status" == 200 ]] &&
    jq -e --arg n "$name" --arg v "$version" \
      '.server.name == $n and .server.version == $v' "$body" >/dev/null 2>&1; then
    echo "${name} ${version} is already in the registry." >&2
    echo "publish=false"
  elif [[ "$status" == 404 ]] &&
    jq -e '.detail == "Server not found"' "$body" >/dev/null 2>&1; then
    echo "${name} ${version} is released and not in the registry yet." >&2
    echo "publish=true"
  else
    fail "Unexpected registry answer for ${name} ${version}: HTTP ${status}, $(head -c 200 "$body")"
  fi
  rm -f "$body"
}

wait_pypi() {
  local marker="mcp-name: ${name}" deadline=$((SECONDS + WAIT_SECONDS))
  until curl -fsS "${PYPI}/pypi/${package}/${version}/json" 2>/dev/null |
    jq -e --arg m "$marker" '(.info.description // "") | contains($m)' >/dev/null 2>&1; do
    if [[ "$SECONDS" -ge "$deadline" ]]; then
      fail "${package} ${version} with '${marker}' not on PyPI after ${WAIT_SECONDS}s." \
        "Publish manually once it is: mcp-publisher login github && mcp-publisher publish"
    fi
    sleep "$POLL_SECONDS"
  done
  echo "${package} ${version} is on PyPI with the mcp-name marker." >&2
}

case "${1:-}" in
  check) check ;;
  wait-pypi) wait_pypi ;;
  *)
    echo "usage: $0 check|wait-pypi" >&2
    exit 2
    ;;
esac
