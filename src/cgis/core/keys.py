"""Data keys: the id of a key node and the rule for when two keys are the same key.

A key is a string the code uses to address data (`address.city`), compared inside a
namespace (an index, a Redis prefix, a model). The namespace is empty for an unscoped
key. DK-1..DK-3 in docs/specs/core/ontology.yml say what writes, reads and declares one.
"""

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
