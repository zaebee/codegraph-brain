"""JSON and YAML files declare data keys (DK-3): one FILE node, then DECLARES_KEY edges.

A document's keys declare themselves as dotted paths, at every depth; nothing below
a list is read, since its items have no key of their own. An OpenSearch mapping
declares its shape instead (`DataProfile.mapping_roots`): each property path, and
`<path>.<subfield>` for each entry under the property's `fields` — so
`status: {type: keyword}` declares `status` and no `status.keyword`.
A mapping declaration carries the property's type as its edge `context` (`keyword`;
`object` for a property with nested `properties`); a plain document's carry none.

This runs on every JSON and YAML file a walk meets, so a file the profile skips is
never parsed, and a parsed one is reduced to its keys and their lines: JSON by a
scan that hands every list and scalar to the C decoder, YAML by composing nodes
(no Python objects are constructed). Every file keeps its FILE node.
"""

import json
import re
from bisect import bisect_right
from collections.abc import Iterator
from fnmatch import fnmatchcase
from json.decoder import scanstring  # type: ignore[attr-defined]  # the C scanner, untyped
from pathlib import PurePosixPath
from typing import Any, override

import structlog
import yaml

from cgis.core.extractor import BaseExtractor
from cgis.core.models import RAW_KEY_PREFIX, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.languages.data.naming import DATA_LANG, file_node_id
from cgis.languages.data.profile import DATA_PROFILE, DataProfile

logger = structlog.getLogger(__name__)

#: A document's keys: each key's line and its value when that is a mapping (its keys) or
#: a string (a mapping property's `type` is read from it); None for anything else.
type Keys = dict[str, tuple[int, Keys | str | None]]
#: Mapping types whose property holds named fields rather than one value.
_OBJECT_TYPES = frozenset({"object", "nested"})

_DECODER = json.JSONDecoder()
_WHITESPACE = re.compile(r"[ \t\n\r]*")
_YAML_LOADER: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_MERGE_KEY = "<<"


class _TooManyKeysError(Exception):
    """A file declares more keys than the profile allows; it then declares none."""


class DataExtractor(BaseExtractor):
    """Reads `.json`, `.yaml` and `.yml` files for the keys they declare."""

    def __init__(
        self, source_roots: list[str] | None = None, profile: DataProfile = DATA_PROFILE
    ) -> None:
        """Keep the source roots for FILE ids and the profile that says what is read."""
        super().__init__(source_roots)
        self._profile = profile

    @override
    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """The FILE node, and a DECLARES_KEY edge from it per declared key."""
        file_node = Node(
            id=file_node_id(file_path, self._pick_source_root(file_path)),
            type=NodeType.FILE,
            name=PurePosixPath(file_path.replace("\\", "/")).name,
            file_path=file_path,
            start_line=1,
            end_line=max(1, code.count("\n") + (0 if code.endswith("\n") else 1)),
            language=DATA_LANG,
            namespace=NodeNamespace.INTERNAL,
        )
        if _skipped(file_path, code, self._profile):
            return [file_node], []
        try:
            documents = _json_keys(code) if file_path.endswith(".json") else _yaml_keys(code)
        except (ValueError, IndexError, RecursionError, yaml.YAMLError) as exc:
            logger.warning("Skipping malformed data file", file_path=file_path, error=str(exc))
            return [file_node], []
        try:
            declared = _declarations(documents, file_path, self._profile)
        except (_TooManyKeysError, RecursionError):
            return [file_node], []
        edges = [
            Edge(
                id=f"{file_path}:declares_{line}_{namespace}|{path}",
                source=file_node.id,
                target=f"{RAW_KEY_PREFIX}{namespace}|{path}",
                type=EdgeType.DECLARES_KEY,
                confidence=1.0,
                file_path=file_path,
                line_number=line,
                context=kind or None,
            )
            for namespace, path, line, kind in declared
        ]
        return [file_node], edges


def _skipped(file_path: str, code: str, profile: DataProfile) -> bool:
    """Whether the profile leaves this file unread: its place, its name, or its size."""
    parts = PurePosixPath(file_path.replace("\\", "/")).parts
    for directory in parts[:-1]:
        if directory in profile.skipped_directories:
            return True
        if directory.startswith(".") and directory not in (".", ".."):
            return True
    name = parts[-1].lower() if parts else ""
    if any(fnmatchcase(name, pattern) for pattern in profile.skipped_names):
        return True
    # A character is at most four bytes: encode only a text near the limit.
    if len(code) > profile.max_bytes:
        return True
    return len(code) * 4 > profile.max_bytes and len(code.encode()) > profile.max_bytes


def _declarations(
    documents: list[Keys], file_path: str, profile: DataProfile
) -> list[tuple[str, str, int, str]]:
    """(namespace, path, line, mapping type) per declared key; the type is "" outside a
    mapping. `_TooManyKeysError` past the profile's cap."""
    linked = profile.mapping_namespace.search(file_path.replace("\\", "/"))
    found: list[tuple[str, str, int, str]] = []
    for keys in documents:
        properties = _mapping_properties(keys, profile)
        if properties is None:
            namespace = ""
            paths: Iterator[tuple[str, int, str]] = (
                (path, line, "") for path, line in _plain_paths(keys, "")
            )
        else:
            namespace = linked.group("index") if linked else ""
            paths = _mapping_paths(properties, "", profile)
        for path, line, kind in paths:
            found.append((namespace, path, line, kind))
            if len(found) > profile.max_keys:
                raise _TooManyKeysError
    return found


def _mapping_properties(keys: Keys, profile: DataProfile) -> Keys | None:
    """The properties of an OpenSearch mapping, or None when the document is not one."""
    for root in profile.mapping_roots:
        here: Keys | None = keys
        for step in root:
            entry = here.get(step) if here is not None else None
            here = entry[1] if entry is not None and isinstance(entry[1], dict) else None
        if here is not None:
            return here
    return None


def _plain_paths(keys: Keys, prefix: str) -> Iterator[tuple[str, int]]:
    """Every key path, dotted, at every depth, outer before inner."""
    for key, (line, child) in keys.items():
        path = f"{prefix}.{key}" if prefix else key
        yield path, line
        if isinstance(child, dict):
            yield from _plain_paths(child, path)


def _mapping_paths(
    properties: Keys, prefix: str, profile: DataProfile
) -> Iterator[tuple[str, int, str]]:
    """Each property path and type, its subfields under `fields`, then its nested properties."""
    for name, (line, spec) in properties.items():
        path = f"{prefix}.{name}" if prefix else name
        if not isinstance(spec, dict):
            yield path, line, ""
            continue
        nested = spec.get(profile.mapping_children)
        children = nested[1] if nested is not None and isinstance(nested[1], dict) else None
        yield path, line, "object" if children is not None else _mapping_type(spec)
        subfields = spec.get(profile.mapping_subfields)
        if subfields is not None and isinstance(subfields[1], dict):
            for subfield, (sub_line, sub_spec) in subfields[1].items():
                kind = _mapping_type(sub_spec) if isinstance(sub_spec, dict) else ""
                yield f"{path}.{subfield}", sub_line, kind
        if children is not None:
            yield from _mapping_paths(children, path, profile)


def _mapping_type(spec: Keys) -> str:
    """A mapping property's type: `object` for an object or nested one, "" when unstated."""
    entry = spec.get("type")
    kind = entry[1] if entry is not None and isinstance(entry[1], str) else ""
    return "object" if kind in _OBJECT_TYPES else kind


def _json_keys(code: str) -> list[Keys]:
    """The keys of a JSON document; none when it is not an object. ValueError when malformed.

    A list or scalar document declares nothing, so it is not read at all.
    """
    text = code.removeprefix("\ufeff")  # a byte order mark
    start = _past_whitespace(text, 0)
    if text[start : start + 1] != "{":
        return []
    newlines = [m.start() for m in re.finditer("\n", text)]
    keys, end = _json_object(text, start, newlines)
    end = _past_whitespace(text, end)
    if end != len(text):
        msg = f"extra data at offset {end}"
        raise ValueError(msg)
    return [keys]


def _past_whitespace(text: str, index: int) -> int:
    """The offset of the first non-whitespace character at or after `index`."""
    match = _WHITESPACE.match(text, index)
    return match.end() if match else index


def _json_object(text: str, index: int, newlines: list[int]) -> tuple[Keys, int]:
    """The object starting at `text[index] == "{"`, and the offset just past it."""
    keys: Keys = {}
    index = _past_whitespace(text, index + 1)
    if text[index] == "}":
        return keys, index + 1
    while True:
        if text[index] != '"':
            msg = f"expecting a key at offset {index}"
            raise ValueError(msg)
        line = bisect_right(newlines, index) + 1
        key, index = scanstring(text, index + 1)
        index = _past_whitespace(text, index)
        if text[index] != ":":
            msg = f"expecting ':' at offset {index}"
            raise ValueError(msg)
        index = _past_whitespace(text, index + 1)
        child: Keys | str | None = None
        if text[index] == "{":
            child, index = _json_object(text, index, newlines)
        else:
            value, index = _DECODER.raw_decode(text, index)  # a list or a scalar, in C
            child = value if isinstance(value, str) else None
        keys[key] = (line, child)
        index = _past_whitespace(text, index)
        if text[index] == ",":
            index = _past_whitespace(text, index + 1)
        elif text[index] == "}":
            return keys, index + 1
        else:
            msg = f"expecting ',' or '}}' at offset {index}"
            raise ValueError(msg)


def _yaml_keys(code: str) -> list[Keys]:
    """The keys of each YAML document that is a mapping; yaml.YAMLError when malformed."""
    return [
        _yaml_mapping(root, {})
        for root in yaml.compose_all(code, Loader=_YAML_LOADER)
        if isinstance(root, yaml.MappingNode)
    ]


def _yaml_mapping(node: yaml.MappingNode, seen: dict[int, Keys]) -> Keys:
    """A mapping node's keys. An alias shares its anchor's keys rather than copying them."""
    keys = seen.get(id(node))
    if keys is not None:
        return keys
    keys = seen[id(node)] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode) or key_node.value == _MERGE_KEY:
            continue
        child: Keys | str | None = None
        if isinstance(value_node, yaml.MappingNode):
            child = _yaml_mapping(value_node, seen)
        elif isinstance(value_node, yaml.ScalarNode):
            child = str(value_node.value)
        keys[str(key_node.value)] = (key_node.start_mark.line + 1, child)
    return keys
