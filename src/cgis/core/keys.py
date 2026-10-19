"""Data keys: the id of a key node and the rule for when two keys are the same key.

A key is a string the code uses to address data (`address.city`), compared inside a
namespace (an index, a Redis prefix, a model). The namespace is empty for an unscoped
key. DK-1..DK-3 in docs/specs/core/ontology.yml say what writes, reads and declares one.

The layer is opt-in (`data_keys_enabled`): measured on owner-api it found few items
worth a look and cost every ingest time, graph size and a stale graph on every JSON
or YAML edit (docs/lab-notes/2026-10-19-data-key-diff-check.md).
"""

import os

#: The environment variable that turns data keys on when no flag or parameter says.
DATA_KEYS_ENV = "CGIS_DATA_KEYS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: What every data key node's id starts with: `key:<namespace>|<path>`.
KEY_ID_PREFIX = "key:"
_SEPARATOR = "|"


def key_id(namespace: str, path: str) -> str:
    """The node id of the key `path` in `namespace` (empty for an unscoped key)."""
    return f"{KEY_ID_PREFIX}{namespace}{_SEPARATOR}{path}"


def parse_key_id(id: str) -> tuple[str, str]:  # noqa: A002 - the spec names it `id`
    """Split a key node id into (namespace, path); inverse of `key_id`.

    The namespace ends at the first separator, so a path may itself contain one.
    """
    namespace, sep, path = id.removeprefix(KEY_ID_PREFIX).partition(_SEPARATOR)
    if not id.startswith(KEY_ID_PREFIX) or not sep:
        msg = f"not a key id: {id!r}"
        raise ValueError(msg)
    return namespace, path


def keys_match(a: str, b: str) -> bool:
    """Whether two key ids can name the same data.

    The paths are equal, and the namespaces are equal or either is unscoped.
    """
    namespace_a, path_a = parse_key_id(a)
    namespace_b, path_b = parse_key_id(b)
    return path_a == path_b and (namespace_a == namespace_b or not namespace_a or not namespace_b)


def data_keys_enabled(explicit: bool | None, recorded: bool | None = None) -> bool:
    """Whether an ingest extracts data keys.

    An explicit flag or parameter wins; then `CGIS_DATA_KEYS`, when set; then the
    mode the graph was last ingested in (`recorded`, so an incremental run or a
    refresh keeps it); else off.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get(DATA_KEYS_ENV, "").strip().lower()
    if env:
        return env in _TRUTHY
    return bool(recorded)
