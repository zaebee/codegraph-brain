"""Go's answers to the core clauses that need per-language facts (`profile.yml`)."""

from importlib import resources
from types import MappingProxyType

import yaml


def _load() -> dict[str, object]:
    text = resources.files("cgis.languages.go").joinpath("profile.yml").read_text(encoding="utf-8")
    loaded: dict[str, object] = yaml.safe_load(text)
    return loaded


def _invokers(profile: dict[str, object]) -> MappingProxyType[str, frozenset[int]]:
    section = profile["callable_values"]
    assert isinstance(section, dict)
    table: dict[str, list[int]] = section["invokers"]
    return MappingProxyType({target: frozenset(positions) for target, positions in table.items()})


#: CV-1: resolved invoker target -> the argument positions it always invokes.
INVOKERS = _invokers(_load())
#: The last segment of every invoker: a call named otherwise passes no value worth a raw edge.
INVOKER_NAMES = frozenset(target.rsplit(".", 1)[-1] for target in INVOKERS)
