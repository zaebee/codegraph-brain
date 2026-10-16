"""The exp4 gold model over an exp4cg dump (see main.go for the dump format).

Pre-registration (docs/lab-notes/2026-10-16-exp4-go-pre-registration.md, section Gold):
- static calls, `go` and `defer` calls are edges (VTA; identical in CHA for static calls);
- closures, `$bound` and `$thunk` fold into their function (done by exp4cg's keys);
- promoted-method wrappers (`synth:` keys) are traversed but never gold symbols;
- a call on an in-module interface takes the CHA implementations, each CHA-only edge adjudicated
  (`adjudications.json`); a call on a stdlib, third-party or anonymous interface takes VTA edges;
- a call through a function value is not an edge; a function value handed straight to a goroutine
  launcher counts as a call by the function that hands it over (`A` records); any other function
  value taken of a closure member ends the chain there (`R` records are not walked), and the
  function that takes it is allowed, not required (amendment 2);
- required = every non-test, non-helper, non-synthetic function in the transitive caller closure;
  chains are not followed through test helpers.

Everything here is a pure function of the dump and the adjudication file, iterated in sorted order.
"""

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

#: pre-registered: no closure root may be one of these methods
PROTOCOL_METHODS = frozenset({"Read", "Write", "Close", "ReadFrom", "WriteTo", "ReadAt", "String", "Error"})


@dataclass(frozen=True)
class In:
    """One incoming edge of the gold graph: `caller` reaches the callee at `site`."""

    caller: str
    kind: str  # static | go | defer | invoke | launch | ref
    site: str
    iface: str = ""  # invoke: interface type
    scope: str = ""  # invoke: inmod | std | ext | anon
    cha_only: bool = False  # invoke on an in-module interface, CHA has it and VTA does not


@dataclass
class Graph:
    """Nodes and reverse edges of one dump under the gold model."""

    goos: str
    funcs: dict[str, dict] = field(default_factory=dict)
    into: dict[str, list[In]] = field(default_factory=lambda: defaultdict(list))
    rejected: list[tuple[In, str, str]] = field(default_factory=list)  # (edge, callee, reason)
    unmatched: list[tuple[In, str]] = field(default_factory=list)  # CHA-only edges no rule names
    verdicts: dict[tuple[str, str, str], dict] = field(default_factory=dict)  # (caller, callee, site)

    def is_synth(self, n: str) -> bool:
        """Traversed, never a gold symbol: a promoted wrapper, or a synthetic package initialiser
        (it stands for package-level `var x = f()` declarations and for the import-order calls
        of other packages' initialisers; a user-written `func init()` is `init#N`, and is a symbol)."""
        flags = self.funcs.get(n, {}).get("flags", ())
        return n.startswith("synth:") or "synth" in flags or "pkginit" in flags

    def file(self, n: str) -> str:
        return self.funcs.get(n, {}).get("file", "")


def typ_of(n: str) -> str:
    """The receiver a method key belongs to, as adjudication rules name it: `pkg/x.T` for a
    method, `(*pkg/x.T)` or `(pkg/x.T)` for a promoted wrapper."""
    if n.startswith("synth:"):
        s = n[len("synth:"):]
        return s[: s.index(")") + 1] if s.startswith("(") else s.rsplit(".", 1)[0]
    return n.rsplit(".", 1)[0]


def _site_matches(site: str, pats: list[str]) -> bool:
    return any(site == p or site.rsplit(":", 1)[0] == p for p in pats)


def adjudicate(rules: list[dict], caller: str, callee: str, iface: str, site: str) -> dict | None:
    """The first rule matching a CHA-only edge: same interface and receiver, and its site in
    `sites` (a file, or file:line; `*` for any) and not in `except`."""
    for r in rules:
        if r["iface"] != iface or r["type"] != typ_of(callee):
            continue
        if r["sites"] != ["*"] and not _site_matches(site, r["sites"]):
            continue
        if _site_matches(site, r.get("except", [])):
            continue
        return r
    return None


def load(dump: Path, adjudications: Path | None = None, require_all: bool = False) -> Graph:
    """Read a dump into the gold graph, applying the CHA-only verdicts in `adjudications`.

    With `require_all`, every CHA-only edge must be matched by a rule (the linux gold build);
    otherwise an unmatched one is accepted and listed in `Graph.unmatched` (the darwin check,
    whose darwin-only implementations have no rules: accepting them can only drop more targets).
    """
    rules = json.loads(adjudications.read_text())["rules"] if adjudications else []
    g = Graph(goos="")
    static: set[tuple[str, str, str, str]] = set()
    inv: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for line in dump.read_text().splitlines():
        p = line.split("\t")
        if p[0] == "H":
            g.goos = p[1]
        elif p[0] == "F":
            rec = {"file": p[2], "line": int(p[3]), "flags": tuple(x for x in p[4].split(",") if x)}
            if g.funcs.get(p[1], rec) != rec:
                raise ValueError(f"two functions share the key {p[1]}: {g.funcs[p[1]]} and {rec}")
            g.funcs[p[1]] = rec
        elif p[0] == "E" and p[1] == "vta" and p[4] in ("static", "go", "defer"):
            static.add((p[2], p[3], p[4], p[5]))
        elif p[0] == "I":
            _, graph, a, b, iface, scope, site = p
            inv[(a, b, iface, scope, site)].add(graph)
        elif p[0] == "R":
            g.into[p[2]].append(In(p[1], "ref", p[3], iface=p[4]))
        elif p[0] == "A":
            g.into[p[2]].append(In(p[1], "launch", p[3], iface=p[4]))
    for a, b, k, site in sorted(static):
        g.into[b].append(In(a, k, site))
    for (a, b, iface, scope, site), graphs in sorted(inv.items()):
        if scope == "inmod":
            e = In(a, "invoke", site, iface, scope, cha_only="vta" not in graphs)
        elif "vta" in graphs:
            e = In(a, "invoke", site, iface, scope)
        else:
            continue
        if e.cha_only:
            r = adjudicate(rules, a, b, iface, site)
            if r is None:
                if require_all:
                    raise ValueError(f"unadjudicated CHA-only edge: {a} -> {b} @{site} [{iface}]")
                g.unmatched.append((e, b))
            else:
                g.verdicts[(a, b, site)] = r
                if r["verdict"] == "reject":
                    g.rejected.append((e, b, r["reason"]))
                    continue
        g.into[b].append(e)
    for b in g.into:
        g.into[b] = sorted(set(g.into[b]), key=lambda e: (e.caller, e.kind, e.site, e.iface))
    return g


@dataclass
class Closure:
    """The reverse closure of one target, with what eligibility needs."""

    target: str
    members: set[str]  # every caller reached, helpers included, target excluded
    required: list[str]
    helpers: list[str]
    files: list[str]
    used: list[tuple[str, In]]  # (callee, edge) for every edge the closure walked
    refs: list[tuple[str, In]]  # function values taken of the target or a member (not walked)
    referencers: list[str]  # the non-helper, non-synthetic functions that take them: allowed
    roots: list[str]
    dyn_only: list[str]


def closure(g: Graph, t: str, is_helper, drop_kinds: frozenset[str] = frozenset()) -> tuple[set[str], list]:
    """Callers of `t`, transitively; a helper is reached but not expanded."""
    seen = {t}
    used = []
    q = deque([t])
    while q:
        n = q.popleft()
        for e in g.into.get(n, []):
            if e.kind in drop_kinds:
                continue
            used.append((n, e))
            if e.caller not in seen:
                seen.add(e.caller)
                if not is_helper(e.caller):
                    q.append(e.caller)
    seen.discard(t)
    return seen, used


#: amendment 2: a function-value reference is not an edge; the referenced function is an entry point
NOT_WALKED = frozenset({"ref"})


def analyse(g: Graph, t: str, is_helper) -> Closure:
    members, used = closure(g, t, is_helper, drop_kinds=NOT_WALKED)
    required = sorted(n for n in members if not is_helper(n) and not g.is_synth(n))
    helpers = sorted(n for n in members if is_helper(n))
    files = sorted({g.file(n) for n in required})
    walk = {t} | members
    refs = [(n, e) for n in sorted(walk) if not is_helper(n) for e in g.into.get(n, []) if e.kind in NOT_WALKED]
    referencers = sorted({e.caller for _, e in refs} - walk - {n for n in g.funcs if g.is_synth(n) or is_helper(n)})
    roots = sorted(n for n in walk if not is_helper(n)
                   and not [e for e in g.into.get(n, []) if e.kind not in NOT_WALKED and not is_helper(e.caller)])
    static_members, _ = closure(g, t, is_helper, drop_kinds=NOT_WALKED | {"invoke"})
    dyn_only = sorted(set(required) - static_members)
    return Closure(t, members, required, helpers, files, used, refs, referencers, roots, dyn_only)
