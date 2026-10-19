"""Time full ingests of one tree with two cgis source trees, interleaved, fresh databases.

Each side runs ``python -m cgis ingest`` with PYTHONPATH set to its ``src`` (no ``-I``), from
the scratch directory, so the two differ only in the code. Prints one line per run and the
medians and ratio.

Usage, run from the scratch directory::

    python scripts/data_keys/ingest_pairs.py --main-src <main checkout>/src [--pairs N]
        [--tree DIR] [--python EXE]

``--tree`` defaults to the owner-api export under the corpus (``locations.py``),
``--python`` to the interpreter running this script; the branch side is this checkout.
"""

import argparse
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from locations import CORPUS, REPO_ROOT

#: The owner-api export the ratio in the lab note was measured on.
DEFAULT_TREE = CORPUS / "owner-2bd0fb7-backend" / "ownima-backend"


def run_once(
    side: str, sides: Mapping[str, str], python: str, tree: Path, scratch: Path
) -> tuple[float, int]:
    """One ingest into a fresh database; its seconds and the database size in bytes."""
    db = scratch / f"{side}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": sides[side]}
    started = time.monotonic()
    subprocess.run(
        [python, "-m", "cgis", "ingest", str(tree), "--source-root", "app", "-o", str(db)],
        cwd=scratch,
        env=env,
        check=True,
        capture_output=True,
    )
    return time.monotonic() - started, db.stat().st_size


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pairs and print the medians."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--main-src", type=Path, required=True, help="main's src directory")
    parser.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    sides = {"main": str(args.main_src), "branch": str(REPO_ROOT / "src")}
    scratch = Path.cwd()
    times: dict[str, list[float]] = {side: [] for side in sides}
    sizes: dict[str, int] = {}
    for index in range(args.pairs):
        for side in sides:
            seconds, sizes[side] = run_once(side, sides, args.python, args.tree, scratch)
            times[side].append(seconds)
            print(f"{index + 1} {side} {seconds:.1f}s db={sizes[side]}", flush=True)
    medians = {side: statistics.median(v) for side, v in times.items()}
    print(
        f"median main={medians['main']:.1f}s branch={medians['branch']:.1f}s "
        f"ratio={medians['branch'] / medians['main']:.3f} "
        f"db main={sizes['main']} branch={sizes['branch']} "
        f"grow={sizes['branch'] / sizes['main']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
