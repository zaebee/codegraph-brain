"""Exp4 gates as pre-registered (docs/lab-notes/2026-10-16-exp4-go-pre-registration.md, section Gates).

Usage: exp4_gates.py results.jsonl

Both arms come from the one results file. A row with `failed` set is kept out of the medians and
counted. Primary cell: task ids containing `-iface-`; static: `-static-`.
"""

import json
import random
import statistics as st
import sys
from collections import defaultdict

ARMS = ("control", "cgis-forced")


def load(path: str) -> tuple[dict[tuple[str, str], list[dict]], list[dict]]:
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    failed = []
    for line in open(path):
        r = json.loads(line)
        if r["arm"] not in ARMS or not r["task"].startswith("go-exp4-"):
            continue
        if r.get("failed"):
            failed.append(r)
            continue
        by[(r["task"], r["arm"])].append(r)
    return by, failed


def med(rs: list[dict], k: str) -> float:
    return st.median(r[k] or 0 for r in rs)


def cell(t: str) -> str:
    return "iface" if "-iface-" in t else "static" if "-static-" in t else "other"


def gate(name: str, vs: list[dict]) -> None:
    r = [v["ratio"] for v in vs]
    mr, md = st.median(r), st.median(v["drec"] for v in vs)
    rnd = random.Random(0)
    b = sorted(st.median(rnd.choice(r) for _ in r) for _ in range(10000))
    d = [v["drec"] for v in vs]
    bd = sorted(st.median(rnd.choice(d) for _ in d) for _ in range(10000))
    print(f"{name} (n={len(vs)}): median cost ratio {mr:.3f} (95% CI {b[250]:.2f}-{b[9749]:.2f}), "
          f"median Δrecall {md:+.3f} (95% CI {bd[250]:+.2f}-{bd[9749]:+.2f}), "
          f"median precision control {st.median(v['pc'] for v in vs):.2f}, "
          f"cgis-forced {st.median(v['pf'] for v in vs):.2f}")
    if name.startswith("PRIMARY"):
        helps = mr <= 0.80 and md >= -0.02
        print(f"  -> {'HELPS: Go joins the impact-question languages' if helps else 'DOES NOT HELP: Go is structure and metrics only'}")


def main() -> None:
    by, failed = load(sys.argv[1])
    rows = [r for rs in by.values() for r in rs]
    print(f"clean runs {len(rows)}, failed {len(failed)}, "
          f"contaminated {sum(bool(r.get('contaminated')) for r in rows)}, "
          f"cost ${sum(r.get('cost_usd') or 0 for r in rows + failed):.2f}, "
          f"forced sessions with 0 cgis calls "
          f"{sum(1 for r in rows if r['arm'] == 'cgis-forced' and not r.get('cgis_calls'))}")
    tasks = sorted({t for t, _ in by})
    T = {}
    for t in tasks:
        c, f = by.get((t, "control"), []), by.get((t, "cgis-forced"), [])
        if not c or not f:
            print("task without both arms:", t)
            continue
        T[t] = {"ratio": med(f, "cost_usd") / med(c, "cost_usd"),
                "drec": med(f, "recall") - med(c, "recall"),
                "pc": med(c, "precision"), "pf": med(f, "precision"),
                "cc": med(c, "cost_usd"), "cf": med(f, "cost_usd"),
                "nc": len(c), "nf": len(f),
                "calls": med(f, "cgis_calls"), "reads_c": med(c, "files_read"), "reads_f": med(f, "files_read"),
                "turns_c": med(c, "turns"), "turns_f": med(f, "turns"),
                "wall_c": med(c, "wall_s"), "wall_f": med(f, "wall_s")}
    print(f"\n{'task':58} {'n':>3} {'ctl$':>6} {'cgis$':>6} {'ratio':>5} {'Δrec':>6} {'P ctl':>5} {'P cgis':>6} "
          f"{'calls':>5} {'reads':>9} {'turns':>9} {'wall s':>11}")
    for t, v in T.items():
        print(f"{t:58} {v['nc']}/{v['nf']} {v['cc']:6.3f} {v['cf']:6.3f} {v['ratio']:5.2f} {v['drec']:+6.2f} "
              f"{v['pc']:5.2f} {v['pf']:6.2f} {v['calls']:5.1f} {v['reads_c']:4.0f}/{v['reads_f']:<4.0f} "
              f"{v['turns_c']:4.0f}/{v['turns_f']:<4.0f} {v['wall_c']:5.0f}/{v['wall_f']:<5.0f}")
    print()
    iface = [v for t, v in T.items() if cell(t) == "iface"]
    if iface:
        gate("PRIMARY iface", iface)
    static = [v for t, v in T.items() if cell(t) == "static"]
    if static:
        gate("SECONDARY static", static)
    gate("SECONDARY all", list(T.values()))


if __name__ == "__main__":
    main()
