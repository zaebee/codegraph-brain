"""Exp3 gates as pre-registered. Usage: exp3_gates.py forced.jsonl (control is the stored exp1/exp2 full runs)."""
import json, random, statistics as st, sys
from collections import defaultdict

FORCED = sys.argv[1]
CONTROL = ["benchmarks/agent_ab/results-exp1-full.jsonl", "benchmarks/agent_ab/results-exp2-full.jsonl"]
# Declared post-hoc gold corrections to stored control recall (lab notes exp1 C1, exp2 C1).
RECALL_FIX = {("owner-exp1-big-ind-thread-for", 0): 1.0, ("owner-exp1-big-ind-thread-for", 1): 1.0,
              ("owner-exp1-big-ind-thread-for", 2): 0.949}


def load(paths, arm):
    by = defaultdict(list)
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if r["arm"] != arm:
                continue
            r["recall"] = RECALL_FIX.get((r["task"], r["run"]), r["recall"]) if arm == "control" else r["recall"]
            by[r["task"]].append(r)
    return by


ctl, frc = load(CONTROL, "control"), load([FORCED], "cgis-forced")
rows = [r for rs in frc.values() for r in rs]
print(f"forced runs {len(rows)}, errors {sum(1 for r in rows if r.get('is_error') or r.get('subtype') != 'success')}, "
      f"contaminated {sum(r['contaminated'] for r in rows)}, cost ${sum(r['cost_usd'] or 0 for r in rows):.2f}, "
      f"sessions with 0 cgis calls {sum(1 for r in rows if not r.get('cgis_calls'))}")


def med(rs, k):
    return st.median(r[k] for r in rs)


def is_primary(t):
    return t.startswith("owner-exp1-big-ind-") or t.startswith("cgis-exp2-desc-")


T = {}
for t in sorted(set(ctl) & set(frc)):
    T[t] = {"ratio": med(frc[t], "cost_usd") / med(ctl[t], "cost_usd"),
            "drec": med(frc[t], "recall") - med(ctl[t], "recall"),
            "pc": med(ctl[t], "precision"), "pf": med(frc[t], "precision"),
            "cc": med(ctl[t], "cost_usd"), "cf": med(frc[t], "cost_usd"),
            "calls": med(frc[t], "cgis_calls"), "turns_c": med(ctl[t], "turns"), "turns_f": med(frc[t], "turns")}
missing = sorted(set(ctl) ^ set(frc))
if missing:
    print("tasks not in both arms:", missing)
print(f"\n{'task':52} {'ctl$':>6} {'cgis$':>6} {'ratio':>5} {'Δrec':>6} {'P ctl':>5} {'P cgis':>6} {'calls':>5}")
for t, v in T.items():
    print(f"{'*' if is_primary(t) else ' '}{t:51} {v['cc']:6.3f} {v['cf']:6.3f} {v['ratio']:5.2f} {v['drec']:+6.2f} {v['pc']:5.2f} {v['pf']:6.2f} {v['calls']:5.1f}")

prim = [v for t, v in T.items() if is_primary(t)]
r = [v["ratio"] for v in prim]
mr, md = st.median(r), st.median(v["drec"] for v in prim)
rnd = random.Random(0)
b = sorted(st.median(rnd.choice(r) for _ in r) for _ in range(10000))
helps = mr <= 0.80 and md >= -0.02
print(f"\nPRIMARY (n={len(prim)}): median cost ratio {mr:.3f} (95% CI {b[250]:.2f}-{b[9749]:.2f}), "
      f"median Δrecall {md:+.3f} -> {'HELPS (keep positioning)' if helps else 'DOES NOT HELP (kill agent-context positioning)'}")
print(f"PRIMARY precision: control {st.median(v['pc'] for v in prim):.2f}, cgis-forced {st.median(v['pf'] for v in prim):.2f}")


def cell(t):
    p = t.split("-")
    return "-".join(p[:4]) if t.startswith("owner-exp1") else "-".join(p[:3])


groups = defaultdict(list)
for t, v in T.items():
    groups[cell(t)].append(v)
print("\nSECONDARY by cell:")
for g, vs in sorted(groups.items()):
    print(f"  {g:24} n={len(vs):2} median ratio {st.median(v['ratio'] for v in vs):.2f}  Δrecall {st.median(v['drec'] for v in vs):+.2f}")
allr = [v["ratio"] for v in T.values()]
print(f"  ALL n={len(allr)} median ratio {st.median(allr):.2f}")
