"""What the data pack reads in a JSON or YAML file, and what it leaves alone (DK-3).

Data only, no logic: the clause is docs/specs/core/ontology.yml. A Python module
rather than a data file, so the import walk behind both fingerprints (extraction,
#617; review) sees it: a data file is invisible to that walk.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DataProfile:
    """Which data files declare keys, and the document shapes that say which keys."""

    #: A larger file declares nothing: generated data, not a schema (read cost bounded).
    max_bytes: int
    #: A file declaring more keys than this declares none: a data dump, not a schema.
    max_keys: int
    #: Lowercased file names (`fnmatch` patterns) that declare nothing: lock files,
    #: minified bundles and tool manifests, whose keys address no application data.
    skipped_names: tuple[str, ...]
    #: Directories whose files declare nothing; every dot-directory is skipped as well.
    skipped_directories: frozenset[str]
    #: A document holding a mapping at one of these key paths is an OpenSearch mapping:
    #: it declares the paths under it, not its own keys.
    mapping_roots: tuple[tuple[str, ...], ...]
    #: Under a mapped property: its nested properties, and its subfields (`.keyword`).
    mapping_children: str
    mapping_subfields: str
    #: The file-name rule linking a mapping to its index, the key namespace (group `index`).
    mapping_namespace: re.Pattern[str]


DATA_PROFILE = DataProfile(
    max_bytes=256 * 1024,
    max_keys=2000,
    skipped_names=("*lock*", "*.min.json", "package.json", "tsconfig*.json"),
    skipped_directories=frozenset({"node_modules"}),
    mapping_roots=(("mappings", "properties"), ("properties",)),
    mapping_children="properties",
    mapping_subfields="fields",
    mapping_namespace=re.compile(r"(?:^|/)mappings/(?P<index>[^/]+)\.json$"),
)
