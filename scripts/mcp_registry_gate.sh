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

name=$(jq -er .name server.json)
version=$(jq -er .version server.json)
package=$(jq -er '.packages[] | select(.registryType == "pypi") | .identifier' server.json)

check() {
  if ! git ls-remote --exit-code --tags origin "refs/tags/${TAG_PREFIX}${version}" >/dev/null; then
    echo "No tag ${TAG_PREFIX}${version}: not a release, nothing to publish." >&2
    echo "publish=false"
    return
  fi
  local listed
  listed=$(curl -fsS --get "${REGISTRY}/v0/servers" \
    --data-urlencode "search=${name}" --data-urlencode "version=${version}" |
    jq --arg n "$name" --arg v "$version" \
      '[.servers[] | select(.server.name == $n and .server.version == $v)] | length')
  if [ "$listed" -gt 0 ]; then
    echo "${name} ${version} is already in the registry." >&2
    echo "publish=false"
    return
  fi
  echo "${name} ${version} is released and not in the registry yet." >&2
  echo "publish=true"
}

wait_pypi() {
  local marker="mcp-name: ${name}" deadline=$((SECONDS + WAIT_SECONDS))
  until curl -fsS "${PYPI}/pypi/${package}/${version}/json" 2>/dev/null |
    jq -e --arg m "$marker" '.info.description | contains($m)' >/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "::error::${package} ${version} with '${marker}' not on PyPI after ${WAIT_SECONDS}s." \
        "Publish manually once it is: mcp-publisher login github && mcp-publisher publish" >&2
      exit 1
    fi
    sleep "$POLL_SECONDS"
  done
  echo "${package} ${version} is on PyPI with the mcp-name marker." >&2
}

case "${1:-}" in
  check) check ;;
  wait-pypi) wait_pypi ;;
  *) echo "usage: $0 check|wait-pypi" >&2; exit 2 ;;
esac
