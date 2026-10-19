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
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path, PurePosixPath

from cgis.core.extractor import BaseExtractor, LanguagePack
from cgis.core.index import IndexFacts
from cgis.core.models import Node, NodeType
from cgis.core.project import ProjectReader
from cgis.core.resolution import Dispatcher, ResolutionRules, TypeNormalizer


@dataclass(frozen=True)
class Manifest:
    """What the registry knows about a pack without importing it."""

    name: str  #: equals `Node.language` for everything the pack emits
    title: str  #: how people write the language's name: what coverage claims say
    load: Callable[[], LanguagePack]  #: imports the pack; called once, on first use
    extensions: tuple[str, ...]  #: matched against the path suffix, dot included
    never_ingested: re.Pattern[str]  #: this language's test files the walk skips outright
    #: Per node type, the metadata keys other files resolve against; a change to
    #: one forces a rebuild of an incremental run. Declared here so collecting the
    #: union imports no pack (spec, Incremental ingest).
    cross_file_metadata: Mapping[NodeType, tuple[str, ...]] = field(default_factory=dict)
    #: Said with every impact answer rooted in this language, and in the server
    #: instructions: what an agent must not take on trust (exp4, for Go).
    impact_caveat: str = ""
    #: Whether the pack's nodes are code another file can name. False for files that
    #: only declare data: their ids stay out of the resolver's index, so a `data/`
    #: directory of JSON never makes an unresolved `data.get` look internal.
    names_code: bool = True


def _python() -> LanguagePack:
    from cgis.languages.python import PACK  # noqa: PLC0415 — on the first .py file

    return PACK


def _typescript() -> LanguagePack:
    from cgis.languages.typescript import PACK  # noqa: PLC0415 — on the first .ts/.tsx file

    return PACK


def _go() -> LanguagePack:
    from cgis.languages.go import PACK  # noqa: PLC0415 — on the first .go file

    return PACK


def _data() -> LanguagePack:
    from cgis.languages.data import PACK  # noqa: PLC0415 — on the first .json/.yaml/.yml file

    return PACK


MANIFESTS: tuple[Manifest, ...] = (
    Manifest(
        name="python",
        title="Python",
        load=_python,
        extensions=(".py",),
        never_ingested=re.compile(r"\.(test|spec)\.py$", re.IGNORECASE),
        cross_file_metadata={
            # Another file's key names its namespace by an imported constant (DK-1).
            NodeType.FILE: ("import_map", "reexports", "star_imports", "str_constants"),
            NodeType.CLASS: ("self_types",),
            # Another file's parameter typed with the alias reads the type behind it (#550).
            NodeType.VARIABLE: ("annotated_type",),
            # A value another file hands over is a call only while this one calls it (#632).
            NodeType.FUNCTION: ("invokes",),
            NodeType.METHOD: ("invokes",),
        },
    ),
    Manifest(
        name="typescript",
        title="TypeScript",
        load=_typescript,
        extensions=(".ts", ".tsx"),
        # .js/.jsx have no pack, but the walk always skipped their test files;
        # kept so `skips` stays exactly the pattern it replaced.
        never_ingested=re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$", re.IGNORECASE),
    ),
    Manifest(
        name="go",
        title="Go",
        load=_go,
        extensions=(".go",),
        # `_test.go` files are ingested and flagged `is_test`, never skipped.
        never_ingested=re.compile(r"\b\B"),  # matches nowhere
        cross_file_metadata={
            NodeType.FILE: ("import_map",),
            # A field's type and a function's results type calls in other files (#608);
            # a field's name shadows a method promoted from an embedding (#656).
            NodeType.CLASS: ("self_types", "fields"),
            # A package variable's type types calls through it in other files (#643 B6).
            NodeType.VARIABLE: ("var_type",),
            NodeType.FUNCTION: ("returns",),
            NodeType.METHOD: ("returns",),
        },
        # exp4 (docs/lab-notes/2026-10-16-exp4-go-pre-registration.md): the impact gate
        # failed, and the graph missed function values and some interface dispatch.
        # Function values since #686: the held-out check #695 measured a value held in a
        # local and called through it (20/20 pairs); a value handed to a listed invoker had one
        # pair. Only that sentence changed; the rest still rests on exp4.
        impact_caveat=(
            "Go is supported for structure and metrics only. Go callers are a lead, not a "
            "list: confirm them by reading the code. A function value held in a local and "
            "called through it has an edge (20 of 20 held-out calls, cgis #695). One passed to "
            "another function that calls it has an edge only if that function is on cgis's "
            "invoker list (sync.Once.Do, time.AfterFunc, sort.Slice and similar ones are); "
            "this is barely measured. A callback stored for a framework to call (a handler, "
            "cobra's RunE) has no caller: treat it as an entry point. Some interface "
            "dispatch is missing (exp4: measured on one library, golang/crypto, mostly its "
            "ssh package; not measured on applications)."
        ),
    ),
    Manifest(
        name="data",
        title="JSON and YAML",
        load=_data,
        extensions=(".json", ".yaml", ".yml"),
        never_ingested=re.compile(r"\b\B"),  # matches nowhere: lock files keep a FILE node
        names_code=False,
        # DK-3: a declaring file adds keys and nothing a caller could reach, so an
        # answer rooted in one (its FILE node) says what it can and cannot hold.
        impact_caveat=(
            "JSON and YAML files contribute data key declarations only: no symbols, calls "
            "or impact. An OpenSearch mapping declares each property and each subfield "
            "under its fields (status.keyword is declared only by a keyword subfield)."
        ),
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


def impact_caveat(language: str) -> str:
    """The caveat an impact answer on `language` carries; empty when there is none."""
    return next((m.impact_caveat for m in MANIFESTS if m.name == language), "")


def names_code(language: str) -> bool:
    """Whether nodes of `language` are code other files can name; imports no pack.

    True for a language no manifest declares: the virtual nodes and hand-built rows
    that carry none have always been indexed.
    """
    manifest = _BY_NAME.get(language)
    return manifest is None or manifest.names_code


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


def file_node_id(path: str, source_root: str | None) -> str:
    """The id of a path's FILE node, by its own pack; a path no pack reads gets the Python rule."""
    pack = pack_for_path(path) or _load("python")
    return pack.file_node_id(path, source_root)


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


def cross_file_metadata() -> dict[NodeType, tuple[str, ...]]:
    """The union over every manifest, keys in manifest order; imports no pack."""
    merged: dict[NodeType, tuple[str, ...]] = {}
    for manifest in MANIFESTS:
        for node_type, keys in manifest.cross_file_metadata.items():
            known = merged.get(node_type, ())
            merged[node_type] = known + tuple(k for k in keys if k not in known)
    return merged


@cache
def _rules(name: str) -> ResolutionRules:
    return _load(name).resolution()


def dispatcher_for(language: str) -> Dispatcher | None:
    """The dispatch rule of the pack named `language`; None for class hierarchy or no pack."""
    return _rules(language).dispatch if language in _BY_NAME else None


def rules_for(source_file: str | None) -> ResolutionRules | None:
    """The resolution rules of the pack that classifies `source_file`, or None."""
    pack = classifying_pack(source_file)
    return None if pack is None else _rules(pack.name)


@cache
def type_normalizers() -> tuple[TypeNormalizer, ...]:
    """Every pack's type normalizers, in manifest order (step 4, ruling D2).

    Cached: the engine asks for them on every declared type, and the packs'
    rules never change once loaded.
    """
    return tuple(n for m in MANIFESTS for n in _rules(m.name).type_normalizers)


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
