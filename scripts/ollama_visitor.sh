#!/usr/bin/env bash
# Reach an aura worker's Ollama from this host, over loopback only.
#
#   AURA_PUNK_KEY=... scripts/ollama_visitor.sh <worker-id>
#   export GUARDIAN_OLLAMA_HOST=http://127.0.0.1:11435
#
# The Colab side is not this script's job: `aura-worker` already starts Ollama
# and opens an frp **stcp** proxy named `ollama-worker-<worker-id>` on port
# 11434 (see packages/aura-worker/src/aura_worker/tunnel.py in the aura repo).
# What has been missing is the other end — an stcp *visitor* — which is why
# reaching that Ollama has meant a manual kubectl port-forward and a running
# aura release.
#
# This is that visitor. It dials frps directly over loopback, so kubernetes is
# not in the path and nothing needs the aura release to be up.
#
# `bindAddr` is 127.0.0.1 deliberately. Ollama has no authentication, and an
# stcp proxy publishes no port on the server precisely so the model is reachable
# only by a holder of the shared key; re-publishing it on 0.0.0.0 here would
# throw that away and put an open LLM on a public address.
#
# The worker id is random per session (`secrets.token_hex(4)`, not settable from
# the worker UI), so it has to be passed in. The aura worker's Gradio log prints
# it as "Starting Umbilical (STCP Tunnel: ollama-worker-<id>)".

set -euo pipefail

WORKER_ID="${1:-}"
if [[ -z "${WORKER_ID}" ]]; then
  echo "usage: AURA_PUNK_KEY=... $0 <worker-id>" >&2
  echo "  the worker id is in the aura worker's log: 'STCP Tunnel: ollama-worker-<id>'" >&2
  exit 2
fi

FRP_SERVER="${FRP_SERVER:-127.0.0.1}"
FRP_SERVER_PORT="${FRP_SERVER_PORT:-7000}"
BIND_PORT="${BIND_PORT:-11435}"
FRP_VERSION="${FRP_VERSION:-0.61.0}"

: "${FRP_TOKEN:?set FRP_TOKEN (frps auth token, see /opt/caddy/frps.toml)}"
: "${AURA_PUNK_KEY:?set AURA_PUNK_KEY (the punk key the worker was started with)}"

FRPC="$(command -v frpc || true)"
if [[ -z "${FRPC}" ]]; then
  # Same version the worker pins, so both ends speak one protocol revision.
  echo "==> installing frpc ${FRP_VERSION} to ~/.local/bin"
  mkdir -p "${HOME}/.local/bin"
  curl -fsSL -o /tmp/frp.tar.gz \
    "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_amd64.tar.gz"
  tar -xzf /tmp/frp.tar.gz -C /tmp
  install -m 0755 "/tmp/frp_${FRP_VERSION}_linux_amd64/frpc" "${HOME}/.local/bin/frpc"
  FRPC="${HOME}/.local/bin/frpc"
fi

# frp reads secrets only from a file, so the config holds two of them and is
# created private before anything is written into it.
CONFIG="$(mktemp -t frpc-visitor-XXXXXX.toml)"
chmod 600 "${CONFIG}"
trap 'rm -f "${CONFIG}"' EXIT

cat >"${CONFIG}" <<EOF
serverAddr = "${FRP_SERVER}"
serverPort = ${FRP_SERVER_PORT}

[auth]
method = "token"
token = "${FRP_TOKEN}"

[[visitors]]
name = "ollama-visitor-${WORKER_ID}"
type = "stcp"
serverName = "ollama-worker-${WORKER_ID}"
secretKey = "${AURA_PUNK_KEY}"
bindAddr = "127.0.0.1"
bindPort = ${BIND_PORT}
EOF

cat <<EOF
==> visitor up: http://127.0.0.1:${BIND_PORT} -> ollama-worker-${WORKER_ID} (loopback only)

  export GUARDIAN_PROVIDER=ollama
  export GUARDIAN_OLLAMA_HOST=http://127.0.0.1:${BIND_PORT}
  export GUARDIAN_MODEL=<the model the worker pulled>
  export GUARDIAN_OLLAMA_NUM_CTX=40960

EOF

exec "${FRPC}" -c "${CONFIG}"
