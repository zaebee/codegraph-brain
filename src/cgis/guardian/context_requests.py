"""A finder that asks for its own graph context, in two passes (#246 experiment C).

The usual review pushes the depth-2 impact graph of each changed module into the
prompt. This variant asks the finder first: pass 1 sees the same prompt and may
only name up to `MAX_REQUESTS` symbols it wants to read; the harness answers each
from graph.db, and pass 2 is the ordinary finder with those answers added. The
mechanism is the experiment, so it is spelled out rather than tuned:

- a request names a symbol (FQN or bare name, resolved like `cgis_context`) and a
  kind — `context` is the symbol's own brief, `callers` its upstream to depth 2;
- every answer is capped, and so is their sum, so a review cannot balloon;
- a symbol that does not resolve gets a line saying so, never silence, because
  "the graph had nothing" and "the request was lost" must read differently;
- pass 2 runs even when nothing resolved, so the only difference from a one-pass
  review is the section that says what came back.

Off by default (`GUARDIAN_FEATURES=context_requests`); production never sees it.
This module holds the data, the pass-1 prompt text and the graph answers; the two
passes run in `chunked.run_requested_review`, beside the other review shapes.
"""

from pathlib import Path

from pydantic import BaseModel

from cgis.query.context.lookup import LookupKind, answer_lookups

#: Pre-registered on #246 (issuecomment-6311457312); changing one is a new experiment.
MAX_REQUESTS = 5
MAX_ANSWER_CHARS = 4_000
MAX_TOTAL_CHARS = 16_000

RequestKind = LookupKind


class ContextRequest(BaseModel, frozen=True):
    """One symbol the finder wants to read before it reviews."""

    symbol: str
    kind: RequestKind


class ContextRequests(BaseModel, frozen=True):
    """Pass 1's whole reply: the requests, and nothing else."""

    requests: list[ContextRequest]


class RequestOutcome(BaseModel, frozen=True):
    """What one request came back as. `chars` is the answer's size after the cap."""

    symbol: str
    kind: RequestKind
    resolved: str | None
    chars: int


class RequestPass(BaseModel, frozen=True):
    """The record of pass 1: whether it parsed, what each request got, what it cost."""

    parsed: bool
    outcomes: list[RequestOutcome]
    prompt_tokens: int
    completion_tokens: int


REQUEST_SUFFIX = f"""
---
### THIS TURN: ASK FOR CONTEXT — DO NOT REVIEW YET

Before you review, you may read up to {MAX_REQUESTS} symbols from the project's code
graph. Do NOT write findings in this turn; reply only with `requests`. Ask for the
code you would need to decide whether a change above is a defect: a function the
diff calls, a caller whose assumptions the diff may break, a class whose contract it
relies on.

- kind "context": the symbol's source, its enclosing class, its direct callers and callees.
- kind "callers": who calls it, up to two hops upstream.

Name a symbol by its fully qualified name when you can (module.Class.method), else by
its bare name. An empty list is a valid answer when the diff is self-contained.
"""

_RETRY_SUFFIX = (
    "\n\nYour previous reply was not valid JSON for the requested schema "
    "({error}). Reply again with only the JSON object."
)

_NO_GRAPH = "No graph is available for this review, so none of the requests could be answered."
UNPARSED = "The context requests could not be parsed, so nothing was looked up."


def parse_requests(json_text: str) -> list[ContextRequest]:
    """Pass 1's reply as requests; raises pydantic's ValidationError when it is not one."""
    return ContextRequests.model_validate_json(json_text).requests


def retry_prompt(prompt: str, error: Exception) -> str:
    """`prompt` again, with why the previous reply did not parse."""
    return prompt + _RETRY_SUFFIX.format(error=error)


def _cap(text: str, limit: int) -> tuple[str, int]:
    """`text` cut to `limit` characters with a note when cut; the kept size beside it."""
    if len(text) <= limit:
        return text, len(text)
    kept = text[:limit]
    return f"{kept}\n… [truncated at {limit} characters]", len(kept)


def answer_requests(
    db_path: Path | None, requests: list[ContextRequest], source_root: str
) -> tuple[str, list[RequestOutcome]]:
    """Answer the first `MAX_REQUESTS` requests from graph.db, under both caps.

    ``source_root`` locates source files for `context` answers, as for
    `cgis_context`. With no graph every request is reported unresolved.
    """
    taken = requests[:MAX_REQUESTS]
    if db_path is None or not db_path.is_file():
        return _NO_GRAPH, [
            RequestOutcome(symbol=r.symbol, kind=r.kind, resolved=None, chars=0) for r in taken
        ]
    answers = answer_lookups(str(db_path), [(r.symbol, r.kind) for r in taken], source_root)
    sections: list[str] = []
    outcomes: list[RequestOutcome] = []
    remaining = MAX_TOTAL_CHARS
    for request, (resolved, answer) in zip(taken, answers, strict=True):
        if remaining <= 0:
            body, chars = "skipped: the context budget for this review is used up", 0
        else:
            body, chars = _cap(answer, min(MAX_ANSWER_CHARS, remaining))
            remaining -= chars
        outcomes.append(
            RequestOutcome(symbol=request.symbol, kind=request.kind, resolved=resolved, chars=chars)
        )
        target = f"`{resolved}`" if resolved else "unresolved"
        sections.append(f"#### `{request.symbol}` ({request.kind}) → {target}\n{body}")
    if not sections:
        return "You asked for nothing.", outcomes
    return "\n\n".join(sections), outcomes
