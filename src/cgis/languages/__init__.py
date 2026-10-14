"""The language registry: which files cgis reads, and which pack reads them (#344, #146).

One manifest per pack declares everything needed before a file is met — name,
module, extensions, the test files the walk never reads — so nothing imports a
pack until a file of its language turns up. Surfaces and `query` import this
module, never `cgis.languages.<pack>` (the `surface-packs` gate).

Adding a language is a manifest here plus a package beside this file that
exposes `PACK`. Each manifest loads its pack with a static import inside a
function — lazy, yet visible to the review-fingerprint walk, which cannot follow
`importlib` (`test_no_dynamic_imports_in_the_review_path`). A pack's own module
imports no grammar; its `extractor()` does (step 2, ruling S2).

Case-sensitive on purpose: `IngestionPipeline._get_extractor` matches
`filename.endswith(ext)` against these lowercase extensions, so `Foo.TS` is
never parsed. Calling it supported here would derive an FQN that matches no
node — the silent miss #344 removed. `TestCaseSensitivity` pins the agreement.
"""

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path, PurePosixPath

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import IndexFacts
from cgis.core.models import Node
from cgis.core.project import ProjectReader


@dataclass(frozen=True)
class Manifest:
    """What the registry knows about a pack without importing it."""

    name: str  #: equals `Node.language` for everything the pack emits
    load: Callable[[], LanguagePack]  #: imports the pack; called once, on first use
    extensions: tuple[str, ...]  #: matched against the path suffix, dot included
    never_ingested: re.Pattern[str]  #: this language's test files the walk skips outright


def _python() -> LanguagePack:
    from cgis.languages.python import PACK  # noqa: PLC0415 — on the first .py file

    return PACK


def _typescript() -> LanguagePack:
    from cgis.languages.typescript import PACK  # noqa: PLC0415 — on the first .ts/.tsx file

    return PACK


MANIFESTS: tuple[Manifest, ...] = (
    Manifest(
        name="python",
        load=_python,
        extensions=(".py",),
        never_ingested=re.compile(r"\.(test|spec)\.py$", re.IGNORECASE),
    ),
    Manifest(
        name="typescript",
        load=_typescript,
        extensions=(".ts", ".tsx"),
        # .js/.jsx have no pack, but the walk always skipped their test files;
        # kept so `skips` stays exactly the pattern it replaced.
        never_ingested=re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$", re.IGNORECASE),
    ),
)

_BY_EXTENSION: dict[str, Manifest] = {ext: m for m in MANIFESTS for ext in m.extensions}
_BY_NAME: dict[str, Manifest] = {m.name: m for m in MANIFESTS}


def _suffix(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).suffix


@cache
def _load(name: str) -> LanguagePack:
    """Import a pack and return its `PACK` — once per process."""
    return _BY_NAME[name].load()


def is_supported(path: str) -> bool:
    """Whether some pack reads this path. Imports no pack."""
    return _suffix(path) in _BY_EXTENSION


def pack_for_path(path: str) -> LanguagePack | None:
    """The pack for a path's extension, or None — callers branch, never default."""
    manifest = _BY_EXTENSION.get(_suffix(path))
    return None if manifest is None else _load(manifest.name)


def pack_named(name: str) -> LanguagePack | None:
    """The pack whose `Node.language` is `name`, or None."""
    return _load(name) if name in _BY_NAME else None


def pack_for_node(node: Node) -> LanguagePack | None:
    """The pack that emitted `node`, by `Node.language` — never by re-sniffing the path."""
    return pack_named(node.language)


def scope_of(path: str, source_root: str | None) -> str:
    """A path's module FQN by its own pack.

    A path no pack reads (an extensionless `src/cgis/pipeline`) gets the Python
    rule, which is then a plain slash-to-dot rewrite — the fallback the CLI has
    always used, kept here so it lives in one place.
    """
    pack = pack_for_path(path) or _load("python")
    return pack.scope_of(path, source_root)


def project_readers(root: Path, extractors: Mapping[str, BaseExtractor]) -> list[ProjectReader]:
    """Every pack's project reader, in manifest order — imports each pack, loads no grammar."""
    return [_load(m.name).project_reader(root, extractors) for m in MANIFESTS]


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> dict[str, IndexFacts]:
    """Every pack's index facts, by pack name."""
    return {m.name: _load(m.name).index_facts(nodes, state) for m in MANIFESTS}


@lru_cache(maxsize=4096)
def classifying_pack(source_file: str | None) -> LanguagePack | None:
    """The pack that classifies a reference from `source_file`; None when no pack reads it.

    No source means the Python pack: `classify_fqn(fqn)` has always applied the
    Python reading, and removing that is a separate, measured change (spec,
    Index and classification). Cached: classification is on the resolver's hot
    path, and a path's suffix never changes.
    """
    return _load("python") if source_file is None else pack_for_path(source_file)


def skips(filename: str) -> bool:
    """Whether the walk never reads this file (`foo.test.ts`, `foo.spec.py`)."""
    return any(m.never_ingested.search(filename) for m in MANIFESTS)


class _LazyExtractors(Mapping[str, BaseExtractor]):
    """Extension → extractor, each built (and its grammar loaded) on first lookup."""

    def __init__(self, source_roots: list[str]) -> None:
        self._roots = list(source_roots)
        self._built: dict[str, BaseExtractor] = {}

    def __getitem__(self, extension: str) -> BaseExtractor:
        if extension not in self._built:
            manifest = _BY_EXTENSION[extension]  # KeyError for an unknown one, as a dict
            self._built[extension] = _load(manifest.name).extractor(extension, self._roots)
        return self._built[extension]

    def __contains__(self, extension: object) -> bool:
        return extension in _BY_EXTENSION  # Mapping's default would build it

    def __iter__(self) -> Iterator[str]:
        return iter(_BY_EXTENSION)

    def __len__(self) -> int:
        return len(_BY_EXTENSION)


def extractors(source_roots: list[str]) -> Mapping[str, BaseExtractor]:
    """The extension → extractor mapping `IngestionPipeline` takes, built lazily."""
    return _LazyExtractors(source_roots)
