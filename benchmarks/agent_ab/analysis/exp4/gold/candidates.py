"""Eligible exp4 targets, by the pre-registered rules, with the count left after each filter.

Usage: python3 -I candidates.py <linux.tsv> <darwin.tsv> <adjudications.json> <helpers.json> <out.json>

Filters, in order (pre-registration, section Tasks, and its eligibility amendment):
  F1 a concrete, non-test, non-synthetic function or method (not a package initialiser,
     not a promoted wrapper, not in a test-helper package)
  F2 gold of 4 to 25 required symbols spread over 2 to 8 files
  F3 waived by amendment 2: a function-value reference ends a chain instead of excluding the target
  F4 no closure root that is a stdlib-protocol method (Read, Write, Close, ReadFrom, WriteTo,
     ReadAt, String, Error)
  F5 the required caller closure, as (symbol, file) pairs, is identical under GOOS=linux and
     GOOS=darwin (both GOARCH=amd64); gold is the linux closure
Cells:
  iface   a direct caller through dispatch on an in-module interface, and at least one required
          caller reachable only through dispatch; family = that interface (the first, sorted,
          when a target has direct callers through several)
  static  no dispatch edge anywhere in the closure
No cgis output is read here or anywhere upstream.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with -I: only this directory
from goldmodel import PROTOCOL_METHODS, analyse, load  # noqa: E402


def main() -> None:
    linux_p, darwin_p, adj_p, helpers_p, out_p = map(Path, sys.argv[1:6])
    lin = load(linux_p, adj_p, require_all=True)
    dar = load(darwin_p, adj_p)
    assert lin.goos == "linux" and dar.goos == "darwin", (lin.goos, dar.goos)
    helper_pkgs = tuple(json.loads(helpers_p.read_text())["helper_packages"])

    def helper_of(g):
        def is_helper(n: str) -> bool:
            f = g.file(n)
            return f.endswith("_test.go") or any(f.startswith(p + "/") for p in helper_pkgs)
        return is_helper

    lh, dh = helper_of(lin), helper_of(dar)
    counts: dict[str, int] = {}
    rows: dict[str, dict] = {}
    dropped: dict[str, dict] = {}

    f0 = sorted(lin.funcs)
    counts["F0 in-module function nodes (incl. synthetic)"] = len(f0)
    f1 = [t for t in f0 if not lin.is_synth(t) and not lh(t) and not t.rsplit(".", 1)[-1].startswith("init#")
          and "testfile" not in lin.funcs[t]["flags"]]
    an = {t: analyse(lin, t, lh) for t in f1}
    def cell_of(t: str) -> str:
        c = an[t]
        direct = any(e.kind == "invoke" and e.scope == "inmod" for e in lin.into.get(t, []))
        if direct and c.dyn_only:
            return "iface"
        if not any(e.kind == "invoke" for _, e in c.used):
            return "static"
        return "neither"

    stage_cells: dict[str, dict[str, int]] = {}

    def tally(name: str, ts: list[str]) -> None:
        counts[name] = len(ts)
        stage_cells[name] = {k: sum(cell_of(t) == k for t in ts) for k in ("iface", "static", "neither")}

    f2 = [t for t in f1 if 4 <= len(an[t].required) <= 25 and 2 <= len(an[t].files) <= 8]
    tally("F1 concrete non-test non-synthetic", f1)
    tally("F2 gold 4-25 symbols over 2-8 files", f2)
    f3 = f2  # amendment 2: F3 waived
    counts["F3 waived (amendment 2): targets with a function-value reference in the closure"] = sum(
        bool(an[t].refs) for t in f2)

    def proto_roots(c):
        return [r for r in c.roots if r.count(".") >= 2 and r.rsplit(".", 1)[1] in PROTOCOL_METHODS]

    f4 = [t for t in f3 if not proto_roots(an[t])]
    tally("F4 no stdlib-protocol-method root", f4)
    f5 = []
    for t in f4:
        c = an[t]
        lin_set = sorted((n, lin.file(n)) for n in c.required)
        if t not in dar.funcs:
            dropped[t] = {"why": "target not in the darwin build"}
            continue
        d = analyse(dar, t, dh)
        dar_set = sorted((n, dar.file(n)) for n in d.required)
        if lin_set != dar_set:
            dropped[t] = {"why": "closure differs",
                          "linux_only": sorted(set(map(tuple, lin_set)) - set(map(tuple, dar_set))),
                          "darwin_only": sorted(set(map(tuple, dar_set)) - set(map(tuple, lin_set)))}
            continue
        f5.append(t)
    tally("F5 identical required closure under linux and darwin", f5)

    for t in f5:
        c = an[t]
        direct_inmod = sorted({e.iface for e in lin.into.get(t, [])
                               if e.kind == "invoke" and e.scope == "inmod" and e.caller in c.members})
        any_invoke = any(e.kind == "invoke" for _, e in c.used)
        cell = None
        if direct_inmod and c.dyn_only:
            cell = "iface"
        elif not any_invoke:
            cell = "static"
        typ, _, meth = t.rpartition(".")
        is_method = t.rsplit("/", 1)[-1].count(".") == 2
        rows[t] = {
            "cell": cell, "family": direct_inmod[0] if cell == "iface" else None,
            "direct_inmod_ifaces": direct_inmod,
            "file": lin.file(t), "line": lin.funcs[t]["line"],
            "is_method": is_method, "recv": typ if is_method else None, "name": meth,
            "required": c.required, "files": c.files, "helpers": c.helpers, "dyn_only": c.dyn_only,
            "roots": c.roots, "referencers": c.referencers,
            "cha_only_edges": sorted({f"{e.caller} -> {b} @{e.site} [{e.iface}]" for b, e in c.used if e.cha_only}),
        }
    cells = {"iface": sum(r["cell"] == "iface" for r in rows.values()),
             "static": sum(r["cell"] == "static" for r in rows.values()),
             "neither": sum(r["cell"] is None for r in rows.values())}
    dropped_cells = {}
    for t in dropped:
        c = an[t]
        di = any(e.kind == "invoke" and e.scope == "inmod" and e.caller in c.members for e in lin.into.get(t, []))
        k = "iface" if di and c.dyn_only else ("static" if not any(e.kind == "invoke" for _, e in c.used) else "neither")
        dropped[t]["cell_if_kept"] = k
        dropped_cells[k] = dropped_cells.get(k, 0) + 1
    families: dict[str, list[str]] = {}
    for t, r in sorted(rows.items()):
        if r["cell"] == "iface":
            families.setdefault(r["family"], []).append(t)
    out = {"counts": counts, "cells_by_stage": stage_cells, "cells": cells, "platform_dropped": len(dropped),
           "platform_dropped_by_cell": dropped_cells, "families": families,
           "rejected_cha_edges_applied": len(lin.rejected),
           "candidates": rows, "dropped": dropped}
    out_p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    for k, v in counts.items():
        print(f"{v:5d}  {k}  {stage_cells.get(k, '')}")
    print("cells:", cells, "| platform-dropped:", len(dropped), dropped_cells)
    for f, ts in families.items():
        print(f"  family {f}: {len(ts)}")


if __name__ == "__main__":
    main()
