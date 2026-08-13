# Guardian on a remote Ollama (aura worker on a Colab GPU)

Free inference on a borrowed GPU, reachable from the guardian host without
publishing anything to the internet.

The Colab side already exists and is not duplicated here: **`aura-worker`** (in
the aura repo, `packages/aura-worker/`) starts Ollama, pulls the model, and
opens an frp **stcp** proxy named `ollama-worker-<worker-id>` on port 11434.
This repo supplies the piece that was missing — the other end.

## What this is for, and what it is not for

**Use it for** the skeptic, harness smoke-tests, and any iteration you would
rather not pay a provider for. A local cross-family skeptic is the measured
configuration: paired with a cloud finder it cut noise from 26 findings to 7
while holding recall, which a same-family skeptic could not do.

**Do not use it for the finder on a benchmark run.** Local 8B finders measure
0/6 recall on fixtures where `mistral-medium` reaches 0.8. Swapping the finder
also makes the number incomparable with any figure already published, which is a
separate reason not to do it by accident.

## The route

```
Colab (aura-worker):  ollama :11434 ──frpc stcp "ollama-worker-<id>"──►  frps :7000
                                                                            │
host (this repo):     127.0.0.1:11435  ◄── frpc visitor, bindAddr=127.0.0.1 ─┘
```

An stcp proxy binds **no port on the server**: it is reachable only through a
visitor holding the same key. That is the whole security argument, because
Ollama has no authentication of its own — a plain tcp proxy with a `remotePort`
would publish an unauthenticated LLM on a public address for as long as the
notebook runs. The visitor re-publishes it on loopback, so it answers this
machine and nothing else.

`aura-worker` already ships one visitor, `nats-visitor`, but that one runs on the
worker and points at the hub's NATS. Nothing on the consumer side existed, which
is why reaching a worker's Ollama has meant a manual `kubectl port-forward` and a
running aura release. **Kubernetes is not in this path**: the visitor dials frps
over loopback on this host.

## Running it

1. Start the worker in Colab as usual and note the id it prints:
   `--- Starting Umbilical (STCP Tunnel: ollama-worker-<id>) ---`.

2. On this host, with the same punk key the worker was given:

```bash
FRP_TOKEN=... AURA_PUNK_KEY=... scripts/ollama_visitor.sh <worker-id>   # leave running

export GUARDIAN_PROVIDER=ollama
export GUARDIAN_OLLAMA_HOST=http://127.0.0.1:11435
export GUARDIAN_MODEL=qwen3:8b
export GUARDIAN_OLLAMA_NUM_CTX=40960
```

`FRP_TOKEN` is the frps auth token (`/opt/caddy/frps.toml`); `AURA_PUNK_KEY` is
the worker's punk key. Neither is committed.

The worker id has to be passed because it is `secrets.token_hex(4)`, generated
per session and not settable from the worker UI. Making it configurable there
would remove this step — a change for the aura repo, not this one.

## Context window: the failure that does not announce itself

Ollama truncates any prompt longer than `num_ctx` **silently**. For a reviewer
that is not a performance problem but a measurement problem — the finder is shown
a fraction of the diff, recall falls, and every number downstream looks entirely
normal.

`OllamaProvider` refuses rather than returning such a review: if the reported
`prompt_eval_count` reaches `num_ctx`, it raises `PromptTruncatedError` naming
the model and the knob. Only the upper bound is treated as a signal — a *small*
count proves nothing, because Ollama reports fewer evaluated tokens when a prefix
was cached.

Sizing, measured on a 16 GB GPU:

| model | weights | practical `num_ctx` |
|---|---|---|
| 14B-Q4 | ~9 GB | ~16k (CUDA-OOMs at 32k — the KV cache does not fit) |
| 8B-Q4 | ~5 GB | ~32–48k |

Guardian prompts run tens of thousands of tokens, so big-PR work needs an 8B
model with a long context, not the 14B.

`GUARDIAN_NO_GRAPH` is **not** a way to fit a large diff into a small window.
Measured across the bench fixtures it saves 2-6%, because the diff dominates the
prompt and the graph section is small beside it — see
[the local bench notes](GUARDIAN_LOCAL_BENCH.md).

## When it breaks

- **"Server disconnected", HTTP 000.** Usually Colab's idle stop killing the
  runtime — it takes ollama and frpc with it. It is not an out-of-memory; an OOM
  says so explicitly.
- **`PromptTruncatedError`.** Raise `GUARDIAN_OLLAMA_NUM_CTX`, move to a
  longer-context model, or set `GUARDIAN_NO_GRAPH`. Do not raise the window past
  what VRAM holds: an OOM can leave the server unhealthy until restarted.
- **The visitor connects but nothing answers.** Check the worker id against the
  worker's current session — it changes every time — and that both ends carry the
  same punk key.
