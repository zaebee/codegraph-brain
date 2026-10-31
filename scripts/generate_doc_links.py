"""Generate the cross-references between the repository's documents and its code.

The docs are mostly islands: of 117 tracked Markdown files, only seven link to
another document, while 962 links point outside the repository. What they do
share is text — the same modules, the same issue numbers, the same experiment
names — and that is enough to derive a useful graph without asking anyone to
maintain it by hand.

Two outputs, both deterministic and idempotent:

- a ``**Related:**`` / ``**Code:**`` block in each document that has a real
  signal, between ``START_CGIS_RELATED`` / ``END_CGIS_RELATED`` markers;
- ``docs/INDEX.md``: documents by code area, design/plan pairs, and the
  documents no signal reaches (the ones worth archiving or deleting).

Signals, strongest first: a design and its plan share a date and a slug; two
documents share at least two ``#issue`` references; they share at least four
``cgis.<module>`` mentions; their TF-IDF cosine is at least 0.35. A document
with none of them gets no block, rather than a block saying "nothing".

Run ``make docs-links`` after editing a document. CI refreshes the blocks on
every push to ``main`` (``autodoc.yml``), the same way it refreshes the
architecture graph and the MCP reference; ``--check`` is what that workflow and
the tests use to prove a run is a no-op.
"""

from __future__ import annotations

import argparse
import math
import posixpath
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: Markers around the generated block. Hand-written text never lives inside.
START_MARKER = "<!-- START_CGIS_RELATED -->"
END_MARKER = "<!-- END_CGIS_RELATED -->"

#: The generated index, relative to the repository root.
INDEX_PATH = Path("docs/INDEX.md")

#: Documents whose body another generator owns; they are indexed, not injected.
GENERATED_DOCS = frozenset(
    {
        "docs/how-to/MCP_REFERENCE.md",
        "docs/architecture/HOW_IT_WORKS.md",
    }
)

#: Directories that are not part of the document graph.
SKIPPED_DIRS = frozenset({"docs/assets", "docs/superpowers"})

#: The engine's top-level packages, used for the "Code" line.
MODULES = (
    "api",
    "bench",
    "core",
    "guardian",
    "languages",
    "pipeline",
    "query",
    "resolver",
    "storage",
)

#: A `cgis.<module>` mention, or the same package's path.
MODULE_RE = re.compile(r"\bcgis\.([a-z_]+)\b|src/cgis/([a-z_]+)/")
#: A GitHub issue or pull request reference: `#495`.
ISSUE_RE = re.compile(r"#(\d{2,5})\b")
#: A repository path mentioned in prose, for the "Code" line.
PATH_RE = re.compile(
    r"(?<![\w/])((?:src|scripts|\.github|tests|plugin|ui)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)"
)
#: `2026-07-30-chunk-source-filter-design.md` -> its date and base slug.
DATED_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.(?:md|mdx)$")
#: Suffixes that separate a design document from its plan.
_ROLE_SUFFIX_RE = re.compile(r"-(?:design|plan|proposal|spec)$")


class MalformedMarkersError(ValueError):
    """A document names one marker without its pair, so a write would duplicate the block."""

    def __init__(self, document: str, detail: str) -> None:
        super().__init__(f"{document}: {detail}")


#: At most this many document links and code links in one block.
MAX_RELATED = 5
MAX_CODE = 5
#: A cosine below this is noise between two long documents.
COSINE_THRESHOLD = 0.35
#: Fewer shared modules says "both talk about the engine somewhere".
SHARED_MODULE_MIN = 4
#: One shared issue number is a coincidence; two is a conversation.
SHARED_ISSUE_MIN = 2

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_STOP_WORDS = frozenset(
    (
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "as",
        "by",
        "from",
        "at",
        "we",
        "you",
        "not",
        "no",
        "can",
        "if",
        "then",
        "than",
        "into",
        "over",
        "under",
        "so",
        "such",
        "which",
        "who",
        "whom",
        "whose",
        "what",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "only",
        "own",
        "same",
        "very",
        "just",
        "also",
        "but",
        "do",
        "does",
        "did",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "me",
        "my",
        "our",
        "their",
        "its",
        "them",
        "they",
        "he",
        "she",
        "his",
        "her",
    )
)


def tracked_documents(repo: Path) -> list[str]:
    """The repository's Markdown documents, relative POSIX paths, sorted.

    `git ls-files` when the directory is a checkout — untracked drafts are not
    part of the graph — and a plain walk otherwise, which keeps the tests and a
    source tarball working without git.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "docs"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        found = [*((repo / "docs").rglob("*.md")), *((repo / "docs").rglob("*.mdx"))]
        listed = [p.relative_to(repo).as_posix() for p in sorted(found)]
    return sorted(
        p
        for p in listed
        if p.endswith((".md", ".mdx"))
        and not any(p.startswith(f"{d}/") for d in SKIPPED_DIRS)
        and p != INDEX_PATH.as_posix()
    )


def _without_fenced_code(document: str) -> str:
    """The document with fenced code samples removed.

    A fenced sample's `src/a.py` is an example, not a reference this repository
    holds. Inline code is kept: a module is almost always named in backticks,
    and dropping it cost most of the code links in the first run.
    """
    return _CODE_BLOCK_RE.sub("", document)


def _tokens(text: str) -> list[str]:
    return [word.lower() for word in _WORD_RE.findall(text) if word.lower() not in _STOP_WORDS]


def _tfidf(documents: dict[str, str]) -> dict[str, dict[str, float]]:
    """Unit-length TF-IDF vectors, so a cosine is one dot product."""
    tokenised = {name: _tokens(text) for name, text in documents.items()}
    frequencies = Counter()
    for tokens in tokenised.values():
        frequencies.update(set(tokens))
    vectors: dict[str, dict[str, float]] = {}
    total = len(tokenised)
    for name, tokens in tokenised.items():
        counts = Counter(tokens)
        vector = {
            word: (1 + math.log(count)) * math.log((1 + total) / (1 + frequencies[word]))
            for word, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors[name] = {word: value / norm for word, value in vector.items()}
    return vectors


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(word, 0.0) for word, value in left.items())


def _dated_groups(documents: list[str]) -> dict[tuple[str, str], list[str]]:
    """Groups of documents that share a date and a base slug — a design and its plan."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for document in documents:
        match = DATED_NAME_RE.match(posixpath.basename(document))
        if match:
            groups[(match.group(1), _ROLE_SUFFIX_RE.sub("", match.group(2)))].append(document)
    return {key: sorted(value) for key, value in groups.items() if len(value) > 1}


def _add_mutual(scores: dict[str, dict[str, float]], left: str, right: str, bonus: float) -> None:
    """Record one signal between two documents in both directions."""
    scores[left][right] = scores[left].get(right, 0.0) + bonus
    scores[right][left] = scores[right].get(left, 0.0) + bonus


def _dated_scores(documents: list[str], scores: dict[str, dict[str, float]]) -> None:
    """A design and its plan: same date, same base slug."""
    for group in _dated_groups(documents).values():
        for document in group:
            for other in group:
                if other != document:
                    _add_mutual(scores, document, other, 100.0)


def _pair_scores(
    documents: list[str], texts: dict[str, str], scores: dict[str, dict[str, float]]
) -> None:
    """Shared issue numbers and shared code areas, counted over document pairs."""
    issues = {
        name: set(ISSUE_RE.findall(_without_fenced_code(text))) for name, text in texts.items()
    }
    modules = {
        name: {module or path for module, path in MODULE_RE.findall(text)}
        for name, text in texts.items()
    }
    for index, document in enumerate(documents):
        for other in documents[index + 1 :]:
            shared_issues = issues[document] & issues[other]
            if len(shared_issues) >= SHARED_ISSUE_MIN:
                _add_mutual(scores, document, other, 10.0 + len(shared_issues))
            shared_modules = modules[document] & modules[other]
            if len(shared_modules) >= SHARED_MODULE_MIN:
                _add_mutual(scores, document, other, 5.0 + len(shared_modules))


def _cosine_scores(
    documents: list[str], texts: dict[str, str], scores: dict[str, dict[str, float]]
) -> None:
    """Textual neighbours: the weakest signal, and the one that reaches unmarked prose."""
    vectors = _tfidf(texts)
    for index, document in enumerate(documents):
        for other in documents[index + 1 :]:
            similarity = _cosine(vectors[document], vectors[other])
            if similarity >= COSINE_THRESHOLD:
                _add_mutual(scores, document, other, similarity * 10.0)


def _related_scores(documents: list[str], texts: dict[str, str]) -> dict[str, list[str]]:
    """Each document's strongest related documents, strongest signal first."""
    scores: dict[str, dict[str, float]] = defaultdict(dict)
    _dated_scores(documents, scores)
    _pair_scores(documents, texts, scores)
    _cosine_scores(documents, texts, scores)
    return {
        document: [name for _, name in sorted(((-score, name) for name, score in related.items()))][
            :MAX_RELATED
        ]
        for document, related in scores.items()
    }


def _module_links(repo: Path, mentions: set[str]) -> list[tuple[str, str]]:
    """Package links for the modules the text names, as (label, repository path) pairs."""
    links: list[tuple[str, str]] = []
    for module in sorted(mentions):
        if len(links) >= MAX_CODE:
            break
        if module not in MODULES:
            continue
        candidates = (f"src/cgis/{module}/", f"src/cgis/{module}.py")
        match = next((candidate for candidate in candidates if (repo / candidate).exists()), None)
        if match:
            links.append((match, match))
    return links


def _path_links(repo: Path, body: str, seen: set[str]) -> list[tuple[str, str]]:
    """Links to files the text names, skipping any a package link already covers."""
    links: list[tuple[str, str]] = []
    for match in PATH_RE.finditer(body):
        if len(seen) >= MAX_CODE:
            break
        candidate = match.group(1).rstrip(".,)")
        if candidate in seen or not (repo / candidate).is_file():
            continue
        if any(candidate.startswith(directory) for directory in seen if directory.endswith("/")):
            continue
        seen.add(candidate)
        links.append((candidate, candidate))
    return links


def _code_links(repo: Path, document: str, text: str) -> list[tuple[str, str]]:
    """The code a document points at, as (label, relative link) pairs."""
    body = _without_fenced_code(text)
    mentions = {module or path for module, path in MODULE_RE.findall(body)}
    linkable = _module_links(repo, mentions)
    linkable.extend(_path_links(repo, body, {path for path, _ in linkable}))
    return [(label, _relative_link(document, target)) for label, target in linkable]


def _relative_link(document: str, target: str) -> str:
    """A relative link from one document to a repository path, POSIX separators."""
    base = posixpath.dirname(document) or "."
    if target.endswith("/"):
        return posixpath.relpath(target.rstrip("/"), base) + "/"
    return posixpath.relpath(target, base)


def _block(repo: Path, document: str, text: str, related: list[str]) -> str:
    """The generated block for one document, or an empty string when it has nothing."""
    lines: list[str] = [START_MARKER]
    if related:
        links = " · ".join(
            f"[{_label(name)}]({_relative_link(document, name)})" for name in related
        )
        lines += [f"**Related:** {links}", ""]
    code = _code_links(repo, document, text)
    if code:
        links = " · ".join(f"[`{label}`]({link})" for label, link in code)
        lines += [f"**Code:** {links}", ""]
    if len(lines) == 1:
        return ""
    lines += [END_MARKER]
    return "\n".join(lines)


def _label(document: str) -> str:
    """A short readable label for a document: its filename without the date."""
    basename = posixpath.basename(document)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)\.(?:md|mdx)$", basename)
    if match:
        return _ROLE_SUFFIX_RE.sub("", match.group(2)) or match.group(2)
    return basename.rsplit(".", 1)[0]


def _replace_block(text: str, block: str, document: str) -> str:
    """Put `block` between the markers, or strip the markers when there is no block.

    When the markers are already there the block stays where it is: a document
    keeps the section it chose, and a run moves nothing but the block's content.
    """
    has_start = START_MARKER in text
    has_end = END_MARKER in text
    if has_start != has_end:
        marker = START_MARKER if has_start else END_MARKER
        raise MalformedMarkersError(document, f"{marker} has no pair; fix or remove it")
    if has_start:
        start, end = text.find(START_MARKER), text.find(END_MARKER)
        if end < start:
            raise MalformedMarkersError(document, f"{END_MARKER} precedes {START_MARKER}")
        if not block:
            before = text[:start].rstrip("\n")
            after = text[end + len(END_MARKER) :].lstrip("\n")
            return before + ("\n\n" + after if after else "\n")
        return text[:start] + block + text[end + len(END_MARKER) :]
    if not block:
        return text.rstrip("\n") + "\n"
    return text.rstrip("\n") + f"\n\n{block}\n"


def _index(documents: list[str], texts: dict[str, str], related: dict[str, list[str]]) -> str:
    """The generated `docs/INDEX.md`: code areas, pairs, and unattached documents."""
    by_area: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        for module, path in MODULE_RE.findall(_without_fenced_code(texts[document])):
            area = module or path
            if area in MODULES and document not in by_area[area]:
                by_area[area].append(document)
    pairs = _dated_groups(documents)
    orphans = [document for document in documents if not related.get(document)]
    lines = [
        "# Documentation index",
        "",
        "*Generated by `scripts/generate_doc_links.py`. Run `make docs-links` after editing a "
        "document; CI refreshes this file and the `Related` blocks on every push to `main`.*",
        "",
        "## By code area",
        "",
    ]
    for module in MODULES:
        linked = sorted(by_area.get(module, []))
        if not linked:
            continue
        lines.append(f"### `cgis.{module}`")
        lines.append("")
        lines += [
            f"- [{_label(document)}]({_relative_link('docs/INDEX.md', document)})"
            for document in linked
        ]
        lines.append("")
    lines += ["## Design and its plan", ""]
    for group in sorted(pairs.values()):
        lines.append(
            " ↔ ".join(
                f"[{_label(document)}]({_relative_link('docs/INDEX.md', document)})"
                for document in group
            )
        )
    lines += [
        "",
        "## No automatic links",
        "",
        "*No shared date/slug, issue pair, code area or textual neighbour — candidates for the "
        "archive, or for a hand-written cross-reference.*",
        "",
    ]
    lines += [
        f"- [{_label(document)}]({_relative_link('docs/INDEX.md', document)})"
        for document in orphans
    ]
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def build(repo: Path) -> dict[Path, str]:
    """Every file the script owns, mapped to its expected content."""
    documents = tracked_documents(repo)
    texts = {
        document: (repo / document).read_text(encoding="utf-8", errors="replace")
        for document in documents
    }
    related = _related_scores(documents, texts)
    expected: dict[Path, str] = {}
    for document in documents:
        body = _replace_block(texts[document], "", document)
        if document not in GENERATED_DOCS:
            body = _replace_block(
                body,
                _block(repo, document, texts[document], related.get(document, [])),
                document,
            )
        expected[repo / document] = body
    expected[repo / INDEX_PATH] = _index(documents, texts, related)
    return expected


def _generated_fragments(repo: Path, expected: dict[Path, str]) -> list[tuple[Path, str]]:
    """Only the text this script owns: the marker block and the index."""
    fragments: list[tuple[Path, str]] = [(repo / INDEX_PATH, expected[repo / INDEX_PATH])]
    for path, content in expected.items():
        start, end = content.find(START_MARKER), content.find(END_MARKER)
        if start != -1 and end > start:
            fragments.append((path, content[start : end + len(END_MARKER)]))
    return fragments


def broken_links(repo: Path, expected: dict[Path, str]) -> list[str]:
    """Generated links whose target does not exist, so `--check` catches a dead one.

    Only the generated fragments are checked. Hand-written prose in the same
    files is not this script's responsibility, and a false positive there would
    fail CI on a document the change under review did not touch.
    """
    broken: list[str] = []
    link_re = re.compile(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]*)?\)")
    for path, fragment in _generated_fragments(repo, expected):
        for target in link_re.findall(fragment):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(repo).as_posix()}: {target}")
    return broken


def main(argv: list[str] | None = None) -> int:
    """Write the blocks and the index, or with `--check`, report what is stale."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository root (default: current directory)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero instead of writing when a block or the index is stale",
    )
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    expected = build(repo)
    stale = [
        path
        for path, content in expected.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    broken = broken_links(repo, expected)
    if args.check:
        if stale or broken:
            for path in stale:
                print(f"stale: {path.relative_to(repo).as_posix()}", file=sys.stderr)
            for item in broken:
                print(f"broken link: {item}", file=sys.stderr)
            return 1
        print(f"documentation links are current ({len(expected)} files)")
        return 0
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"updated {len(stale)} of {len(expected)} files")
    for item in broken:
        print(f"warning: generated link does not resolve: {item}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
