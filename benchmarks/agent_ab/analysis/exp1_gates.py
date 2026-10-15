"""Exp1 gates, exactly as pre-registered (docs/lab-notes/2026-10-15-exp1-radius-indirection.md)."""
import json
import statistics as st
import sys
from collections import defaultdict

results, drift_path = sys.argv[1], sys.argv[2]
rows = [json.loads(l) for l in open(results)]
drift = json.load(open(drift_path))
by = defaultdict(list)
for r in rows:
    by[r["task"]].append(r)

billing_note = f"runs {len(rows)}, failed {sum(1 for r in rows if r.get('is_error') or r.get('subtype') != 'success')}, total ${sum(r.get('cost_usd') or 0 for r in rows):.2f}"
print(billing_note)

tasks = {}
for t, rs in by.items():
    cell = t.split("owner-exp1-")[1].split("-")[:2]
    tasks[t] = {
        "row": cell[0], "ind": cell[1] == "ind",
        "cost": st.median(r["cost_usd"] for r in rs),
        "recall": st.median(r["recall"] for r in rs),
        "precision": st.median(r["precision"] for r in rs),
        "calls": st.median(r["total_tool_calls"] for r in rs),
        "n": len(rs), **drift[t],
    }

print(f"\n{'task':52} {'cost':>6} {'recall':>6} {'prec':>5} {'calls':>5} {'files':>5} {'drift':>5}")
for t, v in sorted(tasks.items(), key=lambda kv: (kv[1]["row"], kv[1]["ind"], kv[1]["cost"])):
    print(f"{t:52} {v['cost']:6.3f} {v['recall']:6.2f} {v['precision']:5.2f} {v['calls']:5.1f} {v['files']:5} {v['drift']:5.3f}")


def med(sel, key="cost"):
    xs = [v[key] for v in tasks.values() if sel(v)]
    return (st.median(xs), len(xs)) if xs else (float("nan"), 0)


print("\ncell medians (cost, recall):")
for row in ("big", "small"):
    for ind in (False, True):
        c, n = med(lambda v: v["row"] == row and v["ind"] == ind)
        r, _ = med(lambda v: v["row"] == row and v["ind"] == ind, "recall")
        print(f"  {row:5} {'indirect' if ind else 'direct  '} n={n:2} cost {c:.3f} recall {r:.2f}")

big, nb = med(lambda v: v["row"] == "big")
small, ns = med(lambda v: v["row"] == "small")
print(f"\nG1 radius: big {big:.3f} (n={nb}) / small {small:.3f} (n={ns}) = x{big / small:.2f} -> {'PASS' if big >= 1.5 * small else 'FAIL'} (>= x1.5)")

print("G2 indirection (descriptive; big x direct n=2):")
for row in ("big", "small"):
    d, nd = med(lambda v: v["row"] == row and not v["ind"])
    i, ni = med(lambda v: v["row"] == row and v["ind"])
    rd, _ = med(lambda v: v["row"] == row and not v["ind"], "recall")
    ri, _ = med(lambda v: v["row"] == row and v["ind"], "recall")
    print(f"  {row}: indirect/direct cost x{i / d:.2f} (n {ni}/{nd}), recall drop {rd - ri:+.2f}")


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def pearson(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den


vs = list(tasks.values())
c, d, f = ranks([v["cost"] for v in vs]), ranks([v["drift"] for v in vs]), ranks([v["files"] for v in vs])
rcd, rcf, rdf = pearson(c, d), pearson(c, f), pearson(d, f)
partial = (rcd - rcf * rdf) / ((1 - rcf**2) * (1 - rdf**2)) ** 0.5
verdict = "PASS (drift adds nothing)" if abs(partial) < 0.2 else ("FAIL: drift matters, H1 refuted" if abs(partial) >= 0.3 else "INCONCLUSIVE")
print(f"\nG3 Spearman: cost~files {rcf:+.2f}, cost~drift {rcd:+.2f}, drift~files {rdf:+.2f}; partial cost~drift|files {partial:+.2f} -> {verdict}")
allr = [r["recall"] for r in rows]
print(f"G4 ceiling: recall 1.00 in {sum(x >= 0.999 for x in allr)}/{len(allr)} runs; min {min(allr):.2f}; "
      f"{'every cell at 1.00 -> cost-only' if all(v['recall'] >= 0.999 for v in vs) else 'not every task at 1.00'}")
