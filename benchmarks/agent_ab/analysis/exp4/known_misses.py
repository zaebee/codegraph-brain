"""Exp4 known-miss check (pre-registration, Gates 3): for each iface task, the gold symbols that
cgis's own impact answer lacks, so that a recall loss in the forced arm can be traced to the graph
or to the agent.

Usage: known_misses.py <graph.db>   (run from the repository root, with `cgis` on PATH via uv)
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from cgis.bench.agent_task import load_tasks, score_answer

DB = sys.argv[1]
for task in load_tasks(Path("benchmarks/agent_ab/tasks")):
    if not task.id.startswith("go-exp4-crypto-iface-"):
        continue
    target = next(line for line in task.notes.splitlines() if line.startswith("Target "))
    short = target.split()[1]  # `pkg.Type.Method`, the package by its last directory
    file = target.split(" at ")[1].rsplit(":", 1)[0]
    rest = short.split(".", 1)[1]
    with sqlite3.connect(DB) as db:
        ids = [r[0] for r in db.execute("select id from nodes where file_path = ? and id like ?", (file, "%." + rest))
               if r[0].endswith("." + rest)]
    assert len(ids) == 1, (short, file, ids)
    fqn = ids[0]
    out = subprocess.run(["cgis", "impact", fqn, "-d", DB, "-f", "json", "--depth", "100"],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    names = [n["fqn"] for n in data["nodes"]]
    text = "```json\n" + json.dumps({"symbols": names, "files": sorted({n["file"] for n in data["nodes"]})}) + "\n```"
    s = score_answer(task, text)
    print(f"{task.id[15:]:48} cgis recall {s.symbol_recall:.2f} nodes {len(names):3d}  missed: {s.missed}")
