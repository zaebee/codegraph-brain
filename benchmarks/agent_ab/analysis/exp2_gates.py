"""Exp2 gates as pre-registered (paired). Usage: gates2.py results.jsonl pairs.json"""
import json, math, random, statistics as st, sys
from collections import defaultdict

rows = [json.loads(l) for l in open(sys.argv[1])]
pairs = json.load(open(sys.argv[2]))["pairs"]
by = defaultdict(list)
for r in rows:
    by[r["task"]].append(r)
print(f"runs {len(rows)}, errors {sum(1 for r in rows if r.get('is_error') or r.get('subtype') != 'success')}, "
      f"contaminated {sum(r['contaminated'] for r in rows)}, total ${sum(r['cost_usd'] or 0 for r in rows):.2f}")


def tid(fqn, cell):
    return f"cgis-exp2-{cell}-" + fqn.rsplit(".", 1)[-1].strip("_").replace("_", "-")


def med(t, k):
    return st.median(r[k] for r in by[t])


P = []
for n, (d, fd, sd, i, fi, si) in enumerate(pairs, 1):
    td, ti = tid(d, "dir"), tid(i, "ind")
    P.append({"n": n, "d": td, "i": ti, "ratio": med(ti, "cost_usd") / med(td, "cost_usd"),
              "drec": med(td, "recall") - med(ti, "recall"),
              "cd": med(td, "cost_usd"), "ci": med(ti, "cost_usd"), "rd": med(td, "recall"), "ri": med(ti, "recall")})
print(f"\n{'#':>2} {'direct':38} {'cost':>6} {'rec':>5} | {'indirect':38} {'cost':>6} {'rec':>5} | ratio  Δrec")
for p in P:
    print(f"{p['n']:>2} {p['d'][10:]:38} {p['cd']:6.3f} {p['rd']:5.2f} | {p['i'][10:]:38} {p['ci']:6.3f} {p['ri']:5.2f} | {p['ratio']:5.2f} {p['drec']:+.2f}")


def gate(ps, label):
    ratios = [p["ratio"] for p in ps]
    mr = st.median(ratios)
    wins = sum(r > 1 for r in ratios)
    need = math.ceil(0.75 * len(ps))
    v = "PASS" if mr >= 1.3 and wins >= need else ("REFUTED (≤1.1)" if mr <= 1.1 else "INCONCLUSIVE")
    rnd = random.Random(0)
    boots = sorted(st.median(rnd.choice(ratios) for _ in ratios) for _ in range(10000))
    lo, hi = boots[250], boots[9749]
    md = st.median(p["drec"] for p in ps)
    print(f"\n[{label}] n={len(ps)}  G1 cost: median ratio {mr:.2f} (95% CI {lo:.2f}–{hi:.2f}), indirect dearer in {wins}/{len(ps)} (need {need}) -> {v}")
    print(f"[{label}]       G2 recall: median Δ(direct−indirect) {md:+.2f} -> {'PASS' if md >= 0.10 else 'not met'}")


gate(P, "all pairs")
gate([p for p in P if p["n"] != 3], "without pair 3")
allr = [r["recall"] for r in rows if "-desc-" not in r["task"]]
print(f"\nG4 ceiling: per run {sum(x >= 0.999 for x in allr)}/{len(allr)} paired runs at 1.00 (min {min(allr):.2f}); "
      f"per pair-task median: {sum(1 for p in P for x in (p['rd'], p['ri']) if x >= 0.999)}/{2 * len(P)} at 1.00")
print("\nDescriptive (unpaired, dynamic hops):")
for t in sorted(t for t in by if "-desc-" in t):
    print(f"  {t[10:]:36} cost {med(t, 'cost_usd'):.3f} recall {med(t, 'recall'):.2f} precision {med(t, 'precision'):.2f}")
