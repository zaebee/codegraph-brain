"""Gold and task YAMLs for the drawn exp4 targets.

Usage: python3 -I build_tasks.py <linux.tsv> <adjudications.json> <helpers.json> <picks.json> <spec.json> <tasks_dir> <gold_record.json>

spec.json: {"repo", "repo_tag", "sha", "src_root", "modpath", "nested_modules_deleted": [...]}.
Writes <tasks_dir>/go-exp4-<repo_tag>-<cell>-<slug>.yaml and <gold_record.json>
(the latter is a record for the report; the harness loads only *.yaml).

Symbol spellings. The harness folds `(*T).M`, `func (r *T) M`, `pkg/file.go:T.M` and `/`
(src/cgis/bench/agent_task.py normalize_symbol) and matches on whole dotted components, so a
gold spelling is found inside any longer qualified answer. Each gold symbol lists:
  - `dir.Rest` (`limactl.newApp`), `pkgpath.Rest` (`cmd/limactl.newApp`), `file:Rest`;
  - `pkgname.Rest` when the package name differs from its directory (`main.newApp`);
  - the bare `Rest` only when no other function or method of the module has that name, since
    a bare name matches the same name in any package.
`main` is never bare (every package main has one). It is spelled `dir.main` (cgis names it
`cmd.limactl.main`), `pkgpath.main`, `main.main` and `file:main`. Known trade: `dir.main` is also
found inside a path-qualified answer for another symbol of a file named main.go
(`cmd/limactl/main.go:newApp` folds to `cmd.limactl.main.newApp`); leaving it out would miss the
spelling cgis itself uses, which would bias the score against the cgis arm.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with -I: only this directory
from goldmodel import analyse, load  # noqa: E402

QUESTION = (
    "In this repository, the {kind} `{name}` in `{file}` is about to change. Which functions and "
    "methods can end up calling it, directly or transitively? List every one, production code only: "
    "non-test code in this repository's root module (not `*_test.go` files). Follow every chain up to "
    "its entry point (an exported API function, `main`, a function nothing else calls, or a function the "
    "code only passes as a value, such as a cobra `RunE` handler) and name every function or method "
    "on the way.\n"
)


def split(n: str) -> tuple[str, str]:
    """`cmd/limactl/editflags.RegisterCreate` -> (`cmd/limactl/editflags`, `RegisterCreate`)."""
    head, _, tail = n.rpartition("/")
    pkg_last, _, rest = tail.partition(".")
    return (f"{head}/{pkg_last}" if head else pkg_last), rest


def main() -> None:
    dump, adj, helpers_p, picks_p, spec_p, tasks_dir, record_p = map(Path, sys.argv[1:8])
    spec = json.loads(spec_p.read_text())
    g = load(dump, adj, require_all=True)
    helper_pkgs = tuple(json.loads(helpers_p.read_text())["helper_packages"])

    def is_helper(n: str) -> bool:
        f = g.file(n)
        return f.endswith("_test.go") or any(f.startswith(p + "/") for p in helper_pkgs)

    rest_count: dict[str, int] = defaultdict(int)
    for n in g.funcs:
        if not g.is_synth(n):
            rest_count[split(n)[1]] += 1

    def pkgname(n: str) -> str:
        return "main" if "main" in g.funcs[n]["flags"] else split(n)[0].rsplit("/", 1)[-1]

    def spellings(n: str) -> dict:
        pkg, rest = split(n)
        d = pkg.rsplit("/", 1)[-1]
        f = g.file(n)
        if rest == "main":
            return {"id": f"{d}.main", "any_of": [f"{d}.main", f"{pkg}.main", "main.main", f"{f}:main"]}
        forms = [f"{d}.{rest}", f"{pkg}.{rest}", f"{f}:{rest}"]
        if pkgname(n) != d:
            forms.append(f"{pkgname(n)}.{rest}")
        if rest_count[rest] == 1:
            forms.insert(0, rest)
            return {"id": rest, "any_of": forms}
        return {"id": f"{d}.{rest}", "any_of": forms}

    def short(n: str) -> str:
        pkg, rest = split(n)
        return f"{pkg.rsplit('/', 1)[-1]}.{rest}"

    picks = json.loads(picks_p.read_text())["picks"]
    summary = []
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for p in picks:
        t, cell = p["target"], p["cell"]
        c = analyse(g, t, is_helper)
        tf = g.file(t)
        pkg, rest = split(t)
        kind = "method" if "." in rest else "function"
        slug = rest.replace(".", "-").lower()
        tid = f"go-exp4-{spec['repo_tag']}-{cell}-{slug}"
        walk = {t} | c.members
        cha_edges = sorted({(e.caller, b, e.site, e.iface) for b, e in c.used if e.cha_only})
        rejected_here = sorted({(e.caller, b, e.site, why) for e, b, why in g.rejected if b in walk and e.caller in walk})
        gold = {
            "symbols": [spellings(n) for n in c.required],
            "files": c.files,
            "allowed_symbols": sorted({spellings(t)["id"]} | {spellings(n)["id"] for n in c.helpers + c.referencers}),
            "allowed_files": sorted({g.file(n) for n in c.helpers + c.referencers} | ({tf} - set(c.files))
                                    - set(c.files)),
        }
        ev = []
        for n in c.required:
            ins = sorted({f"{short(e.caller)}@{e.site} ({e.kind})" for e in g.into.get(n, []) if e.caller in walk})
            ev.append(f"{short(n)} [{g.file(n)}:{g.funcs[n]['line']}] <- " + ("; ".join(ins) or "(entry point)"))
        tin = sorted({f"{short(e.caller)}@{e.site} ({e.kind})" for e in g.into.get(t, []) if e.caller in walk})
        notes = [
            f"exp4 cell {cell}{' (' + p['family'] + ')' if p.get('family') else ''}, drawn by "
            "benchmarks/agent_ab/analysis/exp4/gold/draw.py (random.Random(4)); no cgis input.",
            f"Target {short(t)} at {tf}:{g.funcs[t]['line']}.",
            "Gold model (pre-registration, section Gold): go/ssa VTA static/go/defer calls; closures, $bound and "
            "$thunk folded into their function; promoted wrappers and synthetic package initialisers traversed, "
            "never symbols; in-module interface dispatch by CHA with every CHA-only edge adjudicated "
            f"({adj.name}); stdlib/third-party/anonymous interface dispatch by VTA only; a "
            "function-value reference is not walked: the referenced function is an entry point and the "
            "function taking the value is allowed, not required (amendment 2).",
            f"Allowed referencers ({len(c.referencers)}): {', '.join(short(x) for x in c.referencers) or 'none'}",
            "Platform: the required closure is identical under GOOS=linux and GOOS=darwin (GOARCH=amd64); gold is linux.",
            f"Nested modules deleted from the worktree before both arms: {', '.join(spec['nested_modules_deleted'])}.",
            f"Target's direct callers: {'; '.join(tin)}",
            f"Reached only through interface dispatch ({len(c.dyn_only)}): {', '.join(short(x) for x in c.dyn_only) or 'none'}",
        ]
        notes += [f"CHA-only edge (accepted, {g.verdicts[(a, b, s)]['reason']}): {short(a)} -> {short(b)} @{s} [{i}]"
                  for a, b, s, i in cha_edges]
        notes += [f"CHA-only edge rejected: {short(a)} -> {short(b)} @{s}: {why}" for a, b, s, why in rejected_here]
        notes += [f"Note: {x}" for x in p.get("notes", [])]
        notes += ["Evidence (callee <- caller@site):", *ev]
        task = {"id": tid, "repo": spec["repo"], "sha": spec["sha"], "src_root": spec["src_root"], "type": "impact",
                "delete_paths": [x.rstrip("/") for x in spec["nested_modules_deleted"]],
                "question": QUESTION.format(kind=kind, name=rest, file=tf), "gold": gold,
                "notes": "\n".join(notes) + "\n"}
        (tasks_dir / f"{tid}.yaml").write_text(
            yaml.safe_dump(task, sort_keys=False, width=110, allow_unicode=True), encoding="utf-8")
        summary.append({"id": tid, "cell": cell, "family": p.get("family"), "target": t,
                        "target_file": tf, "n_required": len(c.required), "n_files": len(c.files),
                        "required": c.required, "files": c.files, "referencers": c.referencers, "dyn_only": c.dyn_only,
                        "cha_only_edges": [list(x) for x in cha_edges],
                        "rejected_edges_in_closure": [list(x) for x in rejected_here]})
        print(f"{tid:55s} req={len(c.required):2d} files={len(c.files)} cha_only={len(cha_edges)} rejected={len(rejected_here)}")
    record_p.write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()
