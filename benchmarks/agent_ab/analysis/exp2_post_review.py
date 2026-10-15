"""Exp2 post-review recompute (#634 review C1): pair 2 dropped, extractors rescored on corrected gold.

Run from the repository root: uv run python benchmarks/agent_ab/analysis/exp2_post_review.py
"""
import json, random, statistics as st
from pathlib import Path
from cgis.bench.agent_task import load_tasks, score_answer
from cgis.bench.transcript import parse_transcript

tasks = {t.id: t for t in load_tasks(Path("benchmarks/agent_ab/tasks"))}
rows = [json.loads(l) for l in open("benchmarks/agent_ab/results-exp2-full.jsonl")]
for r in rows:
    if r["task"].endswith("dir-extractors"):
        tr = Path(r["transcript"])
        if not tr.is_file():  # transcripts are git-ignored; a clean clone has only the stored scores
            print(f"extractors run {r['run']}: transcript absent, stored precision {r['precision']:.2f}")
            continue
        s = score_answer(tasks[r["task"]], parse_transcript(tr.read_text().splitlines()).answer)
        print(f"extractors run {r['run']}: recall {s.recall:.2f} precision {s.precision:.2f} (stored {r['precision']:.2f})")
cost = {}
for r in rows:
    cost.setdefault(r["task"], []).append(r["cost_usd"])
pairs = [("reject-metrics-path", "finder-pass"), ("build-ladder", "sanitize-finder-result"), ("resolve-fqn", "as-terms"),
         ("visible-findings", "get-incoming-edges"), ("loc", "fit-templates"), ("effective-prefix", "fit-config")]
rat = [st.median(cost["cgis-exp2-ind-" + i]) / st.median(cost["cgis-exp2-dir-" + d]) for d, i in pairs]
rnd = random.Random(0)
b = sorted(st.median(rnd.choice(rat) for _ in rat) for _ in range(10000))
m = st.median(rat)
v = "PASS" if m >= 1.3 and sum(x > 1 for x in rat) >= 5 else ("REFUTED" if m <= 1.1 else "INCONCLUSIVE")
print(f"6 pairs: ratios {[round(x, 2) for x in rat]}, median {m:.3f}, 95% CI {b[250]:.2f}-{b[9749]:.2f}, dearer {sum(x > 1 for x in rat)}/6 -> {v}")
gm = 1.0
for x in rat:
    gm *= x
print(f"geometric mean {gm ** (1 / len(rat)):.3f}")
