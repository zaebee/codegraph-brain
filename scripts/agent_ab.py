"""Agent A/B benchmark: the same code questions with and without cgis (#543).

Usage:
    uv run python scripts/agent_ab.py run --dry-run                 # print commands, spend nothing
    uv run python scripts/agent_ab.py run --task cgis-impact-language-for --runs 2
    uv run python scripts/agent_ab.py run --repo owner-api=../owner-api --arm cgis
    uv run python scripts/agent_ab.py report

Each run is a headless `claude -p` in a fresh detached worktree at the task's
pinned commit, so no run sees another's files or a graph it did not build.

- `control`: no MCP servers; any committed or stray graph file is deleted.
- `cgis`:    the cgis MCP server from *this* checkout, the plugin's skills, and a
             graph built by this checkout's `cgis ingest` before the clock starts.

Both arms run under the same PreToolUse hook (`cgis.bench.guard`), the same
allowed tools (Read, Grep, Glob, Bash; no edits, no web, no sub-agents) and a
PATH with no cgis or uv on it. Costs money: every non-dry run is a real session.
Results append to `benchmarks/agent_ab/results.jsonl`, one line per
(task, arm, run); the raw stream-json transcripts go to `benchmarks/agent_ab/
transcripts/`, which is git-ignored.
"""

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cgis.bench.agent_task import AgentTask, extract_answer, load_tasks, score_answer
from cgis.bench.transcript import parse_transcript, run_metrics

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guardian_replay_skeptic import worktree_at

Arm = Literal["control", "cgis"]
ARMS: tuple[Arm, ...] = ("control", "cgis")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BENCH_DIR = _REPO_ROOT / "benchmarks" / "agent_ab"
_BIN = Path(sys.executable).parent


def bin_path(name: str) -> str:
    """An entry point installed next to this interpreter (`.exe` on Windows)."""
    return str(_BIN / (f"{name}.exe" if sys.platform == "win32" else name))


#: Executables that would let an agent reach the graph without MCP. A PATH entry
#: holding any of them is dropped; sqlite3 lives in /usr/bin and is left to the hook.
_HIDDEN_EXECUTABLES = ("cgis", "cgis-mcp", "codegraph-brain", "uv", "uvx")
_GRAPH_FILES = ("graph.db", "graph.json")

ALLOWED_TOOLS = "Read,Grep,Glob,Bash,mcp__cgis"
DISALLOWED_TOOLS = "Write,Edit,NotebookEdit,WebFetch,WebSearch,Task,Agent"
DEFAULT_MODEL = "claude-sonnet-5-5"


def _git(*args: str, cwd: Path = _REPO_ROOT) -> str:
    """Run a git command, return stdout, raise on failure."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def sanitized_path(path: str) -> str:
    """PATH without any directory that holds a cgis or uv executable."""
    kept = [
        d
        for d in path.split(os.pathsep)
        if d and not any((Path(d) / exe).exists() for exe in _HIDDEN_EXECUTABLES)
    ]
    return os.pathsep.join(kept)


def agent_env(environ: dict[str, str]) -> dict[str, str]:
    """The environment a session runs with: sanitized PATH, no active virtualenv."""
    env = {k: v for k, v in environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env["PATH"] = sanitized_path(environ.get("PATH", ""))
    return env


def remove_graph_files(root: Path) -> list[Path]:
    """Delete every graph.db / graph.json under `root` (some repos commit one)."""
    found = [p for name in _GRAPH_FILES for p in root.rglob(name) if ".git" not in p.parts]
    for path in found:
        path.unlink()
    return found


def mcp_config(arm: Arm) -> dict[str, object]:
    """The `--mcp-config` document: empty for control, this checkout's server for cgis."""
    if arm == "control":
        return {"mcpServers": {}}
    return {"mcpServers": {"cgis": {"command": bin_path("cgis-mcp"), "args": []}}}


def hook_settings() -> dict[str, object]:
    """The `--settings` document installing the guard hook on every tool call."""
    command = f"{shlex.quote(sys.executable)} -m cgis.bench.guard"
    return {
        "hooks": {
            "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": command}]}]
        }
    }


def stage_plugin(dest: Path) -> Path:
    """Copy the plugin without its `.mcp.json`: the server comes from `--mcp-config`.

    The shipped `.mcp.json` launches the PyPI release through uvx, which would
    measure a published version rather than this checkout.
    """
    shutil.copytree(_REPO_ROOT / "plugin", dest, ignore=shutil.ignore_patterns(".mcp.json"))
    return dest


def build_command(
    *,
    claude: str,
    prompt: str,
    arm: Arm,
    config_dir: Path,
    model: str,
    effort: str | None,
    max_budget_usd: float,
) -> list[str]:
    """The `claude -p` invocation for one run; writes its config files into `config_dir`."""
    mcp_path = config_dir / "mcp.json"
    settings_path = config_dir / "settings.json"
    mcp_path.write_text(json.dumps(mcp_config(arm)), encoding="utf-8")
    settings_path.write_text(json.dumps(hook_settings()), encoding="utf-8")
    cmd = [
        claude,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_path),
        "--settings",
        str(settings_path),
        "--setting-sources",
        "project",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--disallowedTools",
        DISALLOWED_TOOLS,
        "--no-session-persistence",
        "--max-budget-usd",
        f"{max_budget_usd:.2f}",
    ]
    if effort:
        cmd += ["--effort", effort]
    if arm == "cgis":
        cmd += ["--plugin-dir", str(stage_plugin(config_dir / "plugin"))]
    return cmd


def ingest(worktree: Path, src_root: str) -> float:
    """Build `graph.db` in the worktree with this checkout's cgis; seconds taken."""
    start = time.monotonic()
    try:
        subprocess.run(
            [bin_path("cgis"), "ingest", src_root, "--output", "graph.db"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _msg = f"cgis ingest failed (rc={exc.returncode}):\n{exc.stderr}"
        raise RuntimeError(_msg) from exc
    return time.monotonic() - start


def results_row(
    task: AgentTask,
    arm: Arm,
    run: int,
    transcript_lines: Sequence[str],
    meta: dict[str, object],
) -> dict[str, object]:
    """Score one finished transcript into a results line."""
    transcript = parse_transcript(transcript_lines)
    score = score_answer(task, transcript.answer)
    answer = extract_answer(transcript.answer)
    metrics = run_metrics(transcript, answer.files if answer else [])
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "task": task.id,
        "type": task.type,
        "repo": task.repo,
        "repo_sha": task.sha,
        "arm": arm,
        "run": run,
        **meta,
        "served_model": transcript.model,
        "mcp_servers": transcript.mcp_servers,
        "subtype": transcript.subtype,
        "is_error": transcript.is_error,
        "cost_usd": transcript.cost_usd,
        "turns": transcript.turns,
        "duration_ms": transcript.duration_ms,
        "usage": transcript.usage.model_dump(),
        "residual_context": transcript.residual_context,
        **score.model_dump(),
        **metrics.model_dump(),
    }


def _append_jsonl(path: Path, entry: dict[str, object]) -> None:
    """Append one JSON line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def repo_paths(pairs: Sequence[str]) -> dict[str, Path]:
    """`NAME=PATH` arguments as a mapping; this checkout is always `cgis`."""
    repos = {"cgis": _REPO_ROOT}
    for pair in pairs:
        raw_name, sep, raw_path = pair.partition("=")
        name, path = raw_name.strip(), raw_path.strip()
        if not sep or not name or not path:
            _msg = f"--repo expects NAME=PATH, got {pair!r}"
            raise ValueError(_msg)
        repos[name] = Path(path).expanduser().resolve()
    return repos


def run_session(cmd: list[str], cwd: Path, timeout: int) -> tuple[str, int]:
    """Run one session; a timeout keeps the partial transcript and returns code -1.

    A raised TimeoutExpired would end the whole batch, losing every run after it.
    The partial row still lands, with `subtype` empty, so the timeout is visible.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=agent_env(dict(os.environ)),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"session timed out after {timeout}s: {cwd}", file=sys.stderr)
        partial = exc.stdout or ""
        return (partial.decode("utf-8", "replace") if isinstance(partial, bytes) else partial), -1
    return proc.stdout, proc.returncode


def run_one(task: AgentTask, arm: Arm, run: int, repo: Path, args: argparse.Namespace) -> None:
    """One session: worktree → (ingest) → claude -p → transcript → results line."""
    with worktree_at(task.sha, repo) as wt, tempfile.TemporaryDirectory(prefix="ab-") as tmp:
        removed = remove_graph_files(wt)
        ingest_s = ingest(wt, task.src_root) if arm == "cgis" else 0.0
        cmd = build_command(
            claude=args.claude,
            prompt=task.prompt(),
            arm=arm,
            config_dir=Path(tmp),
            model=args.model,
            effort=args.effort,
            max_budget_usd=args.max_budget_usd,
        )
        if args.dry_run:
            print(f"[{task.id} {arm} #{run}] cwd={wt} removed={len(removed)}")
            print("  " + " ".join([*cmd[:2], "<prompt>", *cmd[3:]]))
            return
        start = time.monotonic()
        stdout, returncode = run_session(cmd, wt, args.timeout)
        wall_s = time.monotonic() - start
    transcript_path = args.transcripts / task.id / f"{arm}-{run}.jsonl"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(stdout, encoding="utf-8")
    meta: dict[str, object] = {
        "model": args.model,
        "effort": args.effort,
        "cgis_sha": _git("rev-parse", "HEAD"),
        "returncode": returncode,
        "ingest_s": round(ingest_s, 2),
        "wall_s": round(wall_s, 2),
        "transcript": str(transcript_path.relative_to(_REPO_ROOT))
        if transcript_path.is_relative_to(_REPO_ROOT)
        else str(transcript_path),
    }
    row = results_row(task, arm, run, stdout.splitlines(), meta)
    _append_jsonl(args.results, row)
    print(
        f"[{task.id} {arm} #{run}] recall={row['recall']:.2f} cost=${row['cost_usd']:.3f} "
        f"calls={row['total_tool_calls']} reads={row['files_read']} "
        f"contaminated={row['contaminated']}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    """`run`: every selected (task, arm, run) in turn."""
    tasks = load_tasks(args.tasks)
    if args.task:
        unknown = sorted(set(args.task) - {t.id for t in tasks})
        if unknown:
            print(f"unknown task ids: {unknown}", file=sys.stderr)
            return 2
        tasks = [t for t in tasks if t.id in args.task]
    repos = repo_paths(args.repo)
    missing = sorted({t.repo for t in tasks} - repos.keys())
    if missing:
        print(f"no --repo NAME=PATH for: {missing}", file=sys.stderr)
        return 2
    arms: list[Arm] = args.arm or list(ARMS)
    for task in tasks:
        for run in range(args.runs):
            for arm in arms:
                run_one(task, arm, run, repos[task.repo], args)
    return 0


def summarize(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Median per (task, arm) over clean runs; contaminated runs are counted, not scored."""
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["task"]), str(row["arm"]))].append(row)
    summary: list[dict[str, object]] = []
    for (task, arm), group in sorted(groups.items()):
        clean = [r for r in group if not r.get("contaminated")]
        entry: dict[str, object] = {
            "task": task,
            "arm": arm,
            "runs": len(group),
            "contaminated": len(group) - len(clean),
        }
        for key in ("recall", "precision", "cost_usd", "total_tool_calls", "files_read", "turns"):
            values = [float(str(r[key])) for r in clean if r.get(key) is not None]
            entry[key] = statistics.median(values) if values else None
        summary.append(entry)
    return summary


def fmt_cell(value: object) -> str:
    """A table cell."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def cmd_report(args: argparse.Namespace) -> int:
    """`report`: a markdown table of medians per (task, arm)."""
    if not args.results.exists():
        print(f"no results at {args.results}", file=sys.stderr)
        return 1
    lines = args.results.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    columns = [
        "task", "arm", "runs", "contaminated", "recall", "precision",
        "cost_usd", "total_tool_calls", "files_read", "turns",
    ]  # fmt: skip
    print("| " + " | ".join(columns) + " |")
    print("|" + "---|" * len(columns))
    for entry in summarize(rows):
        print("| " + " | ".join(fmt_cell(entry[c]) for c in columns) + " |")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI definition."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run sessions and append results")
    run.add_argument("--tasks", type=Path, default=_BENCH_DIR / "tasks")
    run.add_argument("--task", action="append", default=[], help="task id; repeatable")
    run.add_argument("--arm", action="append", choices=ARMS, help="repeatable; default both")
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--repo", action="append", default=[], help="NAME=PATH; repeatable")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--effort", default=None)
    run.add_argument("--max-budget-usd", type=float, default=2.0)
    run.add_argument("--timeout", type=int, default=1800, help="seconds per session")
    run.add_argument("--claude", default=shutil.which("claude") or "claude")
    run.add_argument("--results", type=Path, default=_BENCH_DIR / "results.jsonl")
    run.add_argument("--transcripts", type=Path, default=_BENCH_DIR / "transcripts")
    run.add_argument("--dry-run", action="store_true", help="print commands; spend nothing")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="median per task and arm")
    report.add_argument("--results", type=Path, default=_BENCH_DIR / "results.jsonl")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
