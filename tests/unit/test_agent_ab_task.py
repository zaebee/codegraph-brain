"""Agent A/B tasks and answer scoring (#543)."""

import json
from pathlib import Path

import pytest

from cgis.bench.agent_task import (
    ANSWER_FORMAT,
    AgentTask,
    extract_answer,
    file_matches,
    load_task,
    load_tasks,
    normalize_file,
    normalize_symbol,
    score_answer,
    symbol_matches,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = REPO_ROOT / "benchmarks" / "agent_ab" / "tasks"


def _task(**gold: object) -> AgentTask:
    return AgentTask.model_validate(
        {
            "id": "t",
            "repo": "cgis",
            "sha": "abc",
            "src_root": "src",
            "type": "impact",
            "question": "Who calls f?",
            "gold": gold,
        }
    )


def _answer(symbols: list[str], files: list[str], prose: str = "") -> str:
    return f"{prose}\n\n```json\n{json.dumps({'symbols': symbols, 'files': files})}\n```\n"


# --- the shipped tasks ------------------------------------------------------


def test_shipped_tasks_load_with_unique_ids() -> None:
    tasks = load_tasks(TASKS_DIR)
    assert len(tasks) >= 4
    assert len({t.id for t in tasks}) == len(tasks)


def test_shipped_cgis_keys_name_files_that_exist() -> None:
    """A key naming a moved file can never be met, so every run would score a miss."""
    for task in load_tasks(TASKS_DIR):
        if task.repo != "cgis":
            continue
        for path in task.gold.files + task.gold.allowed_files:
            assert (REPO_ROOT / path).is_file(), f"{task.id}: {path}"


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    text = (TASKS_DIR / "cgis-impact-language-for.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(text, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate task ids"):
        load_tasks(tmp_path)


def test_prompt_appends_the_answer_format() -> None:
    task = load_task(TASKS_DIR / "cgis-impact-language-for.yaml")
    assert task.prompt().endswith(ANSWER_FORMAT)
    assert task.prompt().startswith("In this repository")


def test_bare_gold_symbols_become_single_spelling_entries() -> None:
    task = _task(symbols=["a.b", {"id": "x", "any_of": ["X.y", "Z.y"]}])
    assert [s.id for s in task.gold.symbols] == ["a.b", "x"]
    assert task.gold.symbols[0].any_of == ["a.b"]


# --- answer extraction ------------------------------------------------------


def test_extract_answer_takes_the_last_json_block() -> None:
    text = _answer(["first"], []) + _answer(["second"], ["f.py"])
    answer = extract_answer(text)
    assert answer is not None
    assert answer.symbols == ["second"]
    assert answer.files == ["f.py"]


@pytest.mark.parametrize(
    "text",
    [
        "no block at all",
        "```json\n{not json}\n```",
        "```json\n[1, 2]\n```",
        '```json\n{"symbols": "not-a-list"}\n```',
    ],
)
def test_extract_answer_rejects_missing_or_malformed_blocks(text: str) -> None:
    assert extract_answer(text) is None


# --- normalisation and matching --------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/cgis/cli.py::structure", "src.cgis.cli.structure"),
        ("`cgis.cli.structure()`", "cgis.cli.structure"),
        ("cgis/cli.py:structure", "cgis.cli.structure"),
        ("ui/src/a.tsx::render", "ui.src.a.render"),
    ],
)
def test_normalize_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./src/cgis/cli.py", "src/cgis/cli.py"),
        ("src/cgis/cli.py:843", "src/cgis/cli.py"),
        ("src\\cgis\\cli.py:10-20", "src/cgis/cli.py"),
        ("/src/cgis/cli.py", "src/cgis/cli.py"),
    ],
)
def test_normalize_file(raw: str, expected: str) -> None:
    assert normalize_file(raw) == expected


@pytest.mark.parametrize(
    ("predicted", "gold", "expected"),
    [
        ("cgis.cli.structure", "cli.structure", True),
        ("src/cgis/cli.py::structure", "cli.structure", True),
        ("pkg.ResolverEngine.resolve", "ResolverEngine", True),
        ("cgis.cli.structure_x", "cli.structure", False),
        ("structure", "cli.structure", False),
        ("", "", False),
    ],
)
def test_symbol_matches(predicted: str, gold: str, expected: bool) -> None:
    assert symbol_matches(predicted, gold) is expected


@pytest.mark.parametrize(
    ("predicted", "gold", "expected"),
    [
        ("src/cgis/cli.py", "src/cgis/cli.py", True),
        ("cgis/cli.py", "src/cgis/cli.py", True),
        ("/abs/root/src/cgis/cli.py", "src/cgis/cli.py", True),
        ("cli.py", "src/cgis/cli.py", False),
        ("src/cgis/xcli.py", "src/cgis/cli.py", False),
    ],
)
def test_file_matches(predicted: str, gold: str, expected: bool) -> None:
    assert file_matches(predicted, gold) is expected


# --- scoring ----------------------------------------------------------------


def test_perfect_answer_scores_one() -> None:
    task = _task(symbols=["mod.f", "mod.g"], files=["src/mod.py"], facts=["WAL"])
    score = score_answer(task, _answer(["pkg.mod.f", "pkg.mod.g"], ["src/mod.py"], "uses WAL"))
    assert score.parse_failed is False
    assert score.recall == 1.0
    assert score.precision == 1.0
    assert (score.symbol_recall, score.file_recall, score.fact_recall) == (1.0, 1.0, 1.0)
    assert score.missed == []
    assert score.unexpected == []


def test_partial_answer_reports_what_was_missed_and_what_was_extra() -> None:
    task = _task(
        symbols=["mod.f", {"id": "parse", "any_of": ["A.parse", "B.parse"]}],
        files=["src/mod.py", "src/other.py"],
        facts=["5000"],
        allowed_symbols=["mod.helper"],
    )
    text = _answer(["mod.f", "B.parse", "mod.helper", "mod.wrong"], ["src/mod.py"])
    score = score_answer(task, text)
    assert score.symbol_recall == 1.0
    assert score.file_recall == 0.5
    assert score.fact_recall == 0.0
    assert score.recall == pytest.approx(3 / 5)
    assert score.missed == ["src/other.py", "fact:5000"]
    assert score.unexpected == ["mod.wrong"]
    assert score.precision == pytest.approx(4 / 5)


def test_unparseable_answer_is_flagged_and_scores_zero_precision() -> None:
    task = _task(symbols=["mod.f"])
    score = score_answer(task, "It is called from mod.f.")
    assert score.parse_failed is True
    assert score.recall == 0.0
    assert score.precision == 0.0
    assert score.file_recall is None


def test_task_without_required_items_has_full_recall() -> None:
    score = score_answer(_task(), _answer([], []))
    assert score.recall == 1.0


def test_extract_answer_skips_a_trailing_block_that_is_not_an_answer() -> None:
    text = _answer(["real"], ["a/b.py"]) + '\n```json\n{"example": [1]}\n```\n```json\n[1]\n```'
    answer = extract_answer(text)
    assert answer is not None
    assert answer.symbols == ["real"]


def test_missing_tasks_directory_fails_loudly(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError, match="tasks directory not found"):
        load_tasks(missing)
