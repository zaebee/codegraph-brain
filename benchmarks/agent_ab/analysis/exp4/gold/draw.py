"""The seeded exp4 draw (pre-registration, section Tasks). No cgis input.

Usage: python3 -I draw.py <candidates.json> <picks.json>

iface:  random.Random(4); families in sorted order; within a family the candidates are put in
        seeded order (rng.sample over the sorted list) and taken greedily, one per family, or up to
        3 when the family has 6 or more candidates, each new pick with a method name AND a receiver
        type no earlier pick of that family has. 12 wanted; if the families give fewer, all are taken.
        Surplus (amendment 2): more than 12 picks -> a fresh random.Random(4).sample over the picks,
        in draw order, keeps 12. Shortfall: 8-11 picks run as they are; fewer than 8 -> the primary
        gate is not evaluable on this repository (reported as `iface_evaluable`).
static: a fresh random.Random(4) ("drawn with the same seed"); candidates in seeded order, taken
        greedily so that no two share the target's file, until 6.
"""

import json
import random
import sys
from pathlib import Path

SEED = 4
N_IFACE, N_STATIC = 12, 6
MIN_IFACE = 8  # amendment 2: below this the primary gate is not evaluable on the repository


def main() -> None:
    cand = json.loads(Path(sys.argv[1]).read_text())
    rows = cand["candidates"]
    picks = []
    rng = random.Random(SEED)
    families = cand["families"]
    for fam in sorted(families):
        members = sorted(families[fam])
        k = 3 if len(members) >= 6 else 1
        got, names, types = [], set(), set()
        for t in rng.sample(members, len(members)):
            r = rows[t]
            if r["name"] in names or r["recv"] in types:
                continue
            got.append(t)
            names.add(r["name"])
            types.add(r["recv"])
            if len(got) == k:
                break
        picks += [{"cell": "iface", "family": fam, "target": t} for t in got]
    iface_drawn = len(picks)
    if len(picks) > N_IFACE:
        keep = set(random.Random(SEED).sample(range(len(picks)), N_IFACE))
        picks = [p for i, p in enumerate(picks) if i in keep]
    rng = random.Random(SEED)
    static = sorted(t for t, r in rows.items() if r["cell"] == "static")
    files: set[str] = set()
    for t in rng.sample(static, len(static)):
        if rows[t]["file"] in files:
            continue
        files.add(rows[t]["file"])
        picks.append({"cell": "static", "family": None, "target": t})
        if len(files) == N_STATIC:
            break
    n_if = sum(p["cell"] == "iface" for p in picks)
    n_st = sum(p["cell"] == "static" for p in picks)
    out = {"seed": SEED, "wanted": {"iface": N_IFACE, "static": N_STATIC},
           "drawn": {"iface": n_if, "static": n_st}, "iface_before_surplus_cut": iface_drawn,
           "iface_evaluable": n_if >= MIN_IFACE,
           "pool": {"iface_families": {f: len(v) for f, v in sorted(families.items())},
                    "static_candidates": len(static),
                    "static_distinct_files": len({rows[t]["file"] for t in static})},
           "picks": picks}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("drawn", "iface_before_surplus_cut", "iface_evaluable", "pool")}))
    for p in picks:
        r = rows[p["target"]]
        print(f"{p['cell']:6s} {p['family'] or '-':40s} {p['target']:50s} {r['file']}:{r['line']} "
              f"req={len(r['required'])} files={len(r['files'])}")


if __name__ == "__main__":
    main()
