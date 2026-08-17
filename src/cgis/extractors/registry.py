"""One place that says which extensions cgis understands, and how (#344).

Before this module the answer was spread over four sites that had to agree and
did not: `cli.py` registered the extractors, `ContextCollector` filtered changed
files by `.py`, fenced every file as ```python, and derived FQNs with the
*Python* helper. The last of those is the one that bites — the Python helper
strips `.py` and collapses `/__init__`, so handing it `src/app/foo.ts` yields
`src.app.foo.ts`, an id no extractor ever emitted. `_graph_sections` then finds
nothing, logs at debug, and the review completes looking normal with no graph
context at all.

Adding a language should touch this file and nothing else.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from cgis.extractors.base import BaseExtractor
from cgis.extractors.python_extractor import PythonExtractor
from cgis.extractors.python_extractor import file_path_to_module_fqn as python_module_fqn
from cgis.extractors.typescript_extractor import TypeScriptExtractor
from cgis.extractors.typescript_extractor import file_path_to_module_fqn as typescript_module_fqn


@dataclass(frozen=True)
class Language:
    """Everything the ingest pipeline and the reviewer need for one extension."""

    #: Matched against `PurePosixPath(path).suffix`, so it includes the dot.
    extension: str
    build_extractor: Callable[[list[str]], BaseExtractor]
    #: MUST be the same function the extractor uses to build its FILE node id.
    #: `test_module_fqn_round_trips_against_ingested_nodes` holds them together.
    file_path_to_module_fqn: Callable[[str, str | None], str]
    #: Markdown fence label for full-file context.
    code_fence: str


LANGUAGES: tuple[Language, ...] = (
    Language(
        extension=".py",
        build_extractor=lambda roots: PythonExtractor(source_roots=roots),
        file_path_to_module_fqn=python_module_fqn,
        code_fence="python",
    ),
    Language(
        extension=".ts",
        build_extractor=lambda roots: TypeScriptExtractor(source_roots=roots),
        file_path_to_module_fqn=typescript_module_fqn,
        code_fence="ts",
    ),
    Language(
        extension=".tsx",
        build_extractor=lambda roots: TypeScriptExtractor(tsx=True, source_roots=roots),
        file_path_to_module_fqn=typescript_module_fqn,
        code_fence="tsx",
    ),
)

# `.js`/`.jsx` are deliberately absent. The TypeScript FQN helper strips them,
# but `LANGUAGES` registers no extractor for them, so the graph holds no JS
# nodes. Listing them here would let the collector derive a plausible FQN that
# matches nothing — reintroducing the silent-miss path from the other direction.

_BY_EXTENSION: dict[str, Language] = {lang.extension: lang for lang in LANGUAGES}


def language_for(file_path: str) -> Language | None:
    """The Language for a path's extension, or None when cgis cannot parse it.

    None is the "not our file" answer, and every caller must branch on it rather
    than falling back to a default: a default is precisely how a TypeScript path
    used to acquire a Python FQN.

    Case-sensitive on purpose, and it must stay that way while
    `IngestionPipeline._get_extractor` is — it matches `filename.endswith(ext)`
    against these same lowercase keys, so `Foo.TS` is never parsed and the graph
    holds no node for it. Lowercasing here alone would call such a file
    supported, derive an FQN for it, and miss silently — the exact failure this
    module exists to remove. `TestCaseSensitivity` pins the agreement.
    """
    return _BY_EXTENSION.get(PurePosixPath(file_path.replace("\\", "/")).suffix)


def is_supported(file_path: str) -> bool:
    """Whether cgis has an extractor for this path's extension."""
    return language_for(file_path) is not None


def build_extractors(source_roots: list[str]) -> dict[str, BaseExtractor]:
    """The extension → extractor mapping `IngestionPipeline` takes."""
    return {lang.extension: lang.build_extractor(source_roots) for lang in LANGUAGES}
