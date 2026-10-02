"""Agent A/B tasks, their answer keys, and deterministic answer scoring (#543).

A task is one code question asked of a headless agent, plus a hand-verified answer
key. The key is written from the code, not from the cgis graph: a key derived from
the graph would agree with every edge the resolver gets wrong, and the benchmark
would score the treatment arm against itself.

The agent is told to end its answer with a fenced JSON block naming the symbols
and files it relies on. Scoring reads only that block plus a few literal facts,
so it needs no LLM judge and the same transcript always gets the same score.
"""

import json
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

TaskType = Literal["impact", "flow", "orientation", "audit", "control"]

#: Appended to every question, identically in both arms.
ANSWER_FORMAT = (
    "\n\nWhen you are done, end your answer with one fenced ```json block of the form "
    '{"symbols": [...], "files": [...]}: "symbols" lists the functions, methods or '
    "classes your answer names, as dotted qualified names (module.Class.method); "
    '"files" lists the repository-relative paths of the files they live in. '
    "Do not modify any files."
)

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.S)
_CALL_PARENS = re.compile(r"\(.*\)$")
_LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


class GoldSymbol(BaseModel, frozen=True):
    """One required symbol; any of its spellings counts as found."""

    id: str
    any_of: list[str] = Field(min_length=1)


class Gold(BaseModel, frozen=True):
    """The answer key for one task.

    `symbols`, `files` and `facts` are required: each one missing costs recall.
    `allowed_*` are correct but optional, so naming them costs no precision.
    """

    symbols: list[GoldSymbol] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    allowed_symbols: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)

    @field_validator("symbols", mode="before")
    @classmethod
    def _bare_names(cls, value: object) -> object:
        """Accept a plain string as a symbol whose only spelling is itself."""
        if not isinstance(value, list):
            return value
        return [{"id": v, "any_of": [v]} if isinstance(v, str) else v for v in value]


class AgentTask(BaseModel, frozen=True):
    """One benchmark question pinned to a repository commit."""

    id: str
    repo: str
    sha: str
    src_root: str
    type: TaskType
    question: str
    gold: Gold
    notes: str = ""

    def prompt(self) -> str:
        """The exact text sent to the agent."""
        return self.question.strip() + ANSWER_FORMAT


class AgentAnswer(BaseModel, frozen=True):
    """The structured block parsed from the end of an answer."""

    symbols: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)


class AnswerScore(BaseModel, frozen=True):
    """Recall and precision of one answer against its task's key.

    `recall` pools every required item (symbols, files and facts) so one number
    ranks runs; the per-kind fields say where it came from.
    """

    parse_failed: bool
    recall: float
    precision: float
    symbol_recall: float | None
    file_recall: float | None
    fact_recall: float | None
    missed: list[str]
    unexpected: list[str]


def load_task(path: Path) -> AgentTask:
    """Read one task YAML file."""
    return AgentTask.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_tasks(directory: Path) -> list[AgentTask]:
    """Every `*.yaml` task in a directory, sorted by id; ids must be unique."""
    if not directory.is_dir():
        _msg = f"tasks directory not found: {directory}"
        raise FileNotFoundError(_msg)
    tasks = sorted((load_task(p) for p in directory.glob("*.yaml")), key=lambda t: t.id)
    ids = [t.id for t in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        _msg = f"duplicate task ids: {duplicates}"
        raise ValueError(_msg)
    return tasks


def _answer_from_block(block: str) -> AgentAnswer | None:
    """One fenced block as an AgentAnswer, or None when it is not one."""
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.keys() & {"symbols", "files"}:
        return None
    try:
        return AgentAnswer.model_validate(data)
    except ValueError:
        return None


def extract_answer(text: str) -> AgentAnswer | None:
    """The last fenced JSON block in `text` that is an answer, or None.

    Searched from the end, so an example block quoted earlier in the answer, or a
    non-answer block after it, does not hide the real one.
    """
    for block in reversed(_JSON_BLOCK.findall(text)):
        answer = _answer_from_block(block)
        if answer is not None:
            return answer
    return None


def normalize_symbol(name: str) -> str:
    """Fold the spellings agents use for a symbol into one dotted form.

    `src/cgis/cli.py::structure`, `cgis.cli.structure()` and `cgis/cli.py:structure`
    all become a dotted path ending in `cli.structure`.
    """
    s = name.strip().strip("`").strip()
    s = _CALL_PARENS.sub("", s)
    s = s.replace("::", ".").replace(":", ".").replace("/", ".").replace("\\", ".")
    s = s.replace(".py.", ".").replace(".ts.", ".").replace(".tsx.", ".")
    return s.strip(".")


def normalize_file(path: str) -> str:
    """Repository-relative POSIX form of a path, without a trailing `:line`."""
    s = path.strip().strip("`").strip().replace("\\", "/")
    s = _LINE_SUFFIX.sub("", s)
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def symbol_matches(predicted: str, gold: str) -> bool:
    """True when `predicted` names `gold` on whole dotted components.

    `cgis.cli.structure` names `cli.structure`, and `pkg.ResolverEngine.resolve`
    names the class `ResolverEngine`; `cli.structure_x` names neither.
    """
    p, g = normalize_symbol(predicted), normalize_symbol(gold)
    return bool(g) and f".{g}." in f".{p}."


def file_matches(predicted: str, gold: str) -> bool:
    """True when two paths name the same file, allowing a missing leading directory.

    The shorter side must still contain a directory, so a bare `cli.py` never
    matches: in a real repository a basename alone is ambiguous.
    """
    p, g = normalize_file(predicted), normalize_file(gold)
    if p == g:
        return True
    shorter, longer = (p, g) if len(p) < len(g) else (g, p)
    return "/" in shorter and longer.endswith("/" + shorter)


def _ratio(hit: int, total: int) -> float | None:
    """hit/total, or None when there is nothing to count."""
    return hit / total if total else None


def _missed_symbols(gold: Gold, answer: AgentAnswer) -> list[str]:
    """Ids of required symbols no spelling of which the answer names."""
    return [
        sym.id
        for sym in gold.symbols
        if not any(symbol_matches(p, g) for p in answer.symbols for g in sym.any_of)
    ]


def _missed_files(gold: Gold, answer: AgentAnswer) -> list[str]:
    """Required files the answer does not name."""
    return [f for f in gold.files if not any(file_matches(p, f) for p in answer.files)]


def _missed_facts(gold: Gold, text: str) -> list[str]:
    """Required literal facts absent from the answer text (case-insensitive)."""
    lowered = text.lower()
    return [f"fact:{fact}" for fact in gold.facts if fact.lower() not in lowered]


def score_answer(task: AgentTask, text: str) -> AnswerScore:
    """Score a final answer against the task's key."""
    gold = task.gold
    parsed = extract_answer(text)
    answer = parsed or AgentAnswer()

    missed_symbols = _missed_symbols(gold, answer)
    missed_files = _missed_files(gold, answer)
    missed_facts = _missed_facts(gold, text)
    found_symbols = len(gold.symbols) - len(missed_symbols)
    found_files = len(gold.files) - len(missed_files)
    found_facts = len(gold.facts) - len(missed_facts)

    known_symbols = [g for s in gold.symbols for g in s.any_of] + gold.allowed_symbols
    known_files = gold.files + gold.allowed_files
    unexpected = [
        p for p in answer.symbols if not any(symbol_matches(p, g) for g in known_symbols)
    ] + [p for p in answer.files if not any(file_matches(p, g) for g in known_files)]
    predicted = len(answer.symbols) + len(answer.files)
    required = len(gold.symbols) + len(gold.files) + len(gold.facts)
    found = found_symbols + found_files + found_facts

    return AnswerScore(
        parse_failed=parsed is None,
        recall=found / required if required else 1.0,
        precision=(predicted - len(unexpected)) / predicted if predicted else 0.0,
        symbol_recall=_ratio(found_symbols, len(gold.symbols)),
        file_recall=_ratio(found_files, len(gold.files)),
        fact_recall=_ratio(found_facts, len(gold.facts)),
        missed=missed_symbols + missed_files + missed_facts,
        unexpected=unexpected,
    )
