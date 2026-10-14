"""The Python pack's index facts and classification (spec: Index and classification).

Moved from `cgis.resolver.indices` in language-packs step 3; the history of each
rule is in its docstring and issue number.
"""

import builtins
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

from cgis.core.index import ClassifyContext
from cgis.core.js_globals import JS_BUILTINS_ROOT
from cgis.core.models import Node, NodeNamespace, NodeType

_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_LAYOUT_DIRS = frozenset({"src", "lib"})

#: How many of a root's imports must reach a node before it may be stripped off a
#: target (#498 review). One is not evidence: a third-party module that happens to
#: share a path with ours — `from boto3.utils.helpers import x` against our own
#: `utils/helpers.py` — made `boto3` first-party, and stripping it wired
#: `import boto3.config` into this project's `config.py` at full confidence.
#: Classification itself still accepts one, because there the cost of a wrong call
#: is a namespace label rather than a fabricated internal edge.
_LAYOUT_PREFIX_EVIDENCE = 2


def _strips_to_a_node(imported_fqn: str, node_ids: set[str]) -> bool:
    """True when dropping leading segments of `imported_fqn` reaches a real node.

    `app.domains.models.Thing` against a graph rooted at `domains` strips to
    `domains.models.Thing`, which exists — so `app` is this project's own name,
    not a package it depends on.

    The remainder must keep at least two segments. Stripping to a single one
    matches any library whose submodule shares a name with a top-level module of
    ours — `from pydantic import config` against a project that has `config.py`
    strips to `config`, and the whole of pydantic would then classify INTERNAL,
    breaking resolution for every symbol in it. Two segments cannot collide by
    accident, and a genuine first-party prefix always has deeper imports than
    that: on owner-api 3346 values resolve, none of them needing this.
    """
    parts = imported_fqn.split(".")
    return any(".".join(parts[i:]) in node_ids for i in range(1, len(parts) - 1))


def _heads_under(roots: set[str], node_ids: Iterable[str]) -> frozenset[str]:
    """The `root.second` heads of node ids under `roots`, read past a `src`/`lib` layout.

    The same two spellings `_add_node_to_suffix_map` makes a root internal by.
    """
    if not roots:
        return frozenset()
    heads: set[str] = set()
    for node_id in node_ids:
        parts = node_id.split(".")
        start = 1 if len(parts) > 2 and parts[0] in {"src", "lib"} else 0
        for offset in {0, start}:
            if len(parts) > offset + 1 and parts[offset] in roots:
                heads.add(f"{parts[offset]}.{parts[offset + 1]}")
    return frozenset(heads)


def _is_declared_dependency(
    fqn: str, root: str, facts: "PythonIndexFacts", nodes: Mapping[str, Node]
) -> bool:
    """True when an internal-root `fqn` names a declared dependency, not our package.

    The root alone cannot say: `app/alembic/` makes `alembic` internal, while
    `from alembic import op` is the library (#495). Our package is what it
    holds, so `alembic.env.run_migrations` stays internal and
    `alembic.op.add_column` — `op` being nothing of ours — is the dependency.

    The manifest is what licenses the second reading, never the graph alone:
    a graph-only rule declared `scripts.ci.openapi_snapshot` third-party
    because that module lives outside the ingest root, though it is ours and
    `scripts` is no dependency. The roots come from Python manifests, so only
    a Python source (or an unscoped call) reads them, as in #454.
    """
    if root not in facts.dependency_roots or fqn in nodes:
        return False
    return ".".join(fqn.split(".", maxsplit=2)[:2]) not in facts.dependency_heads


def _split_import_roots(
    file_imports: dict[str, dict[str, str]], node_ids: set[str]
) -> tuple[set[str], set[str], set[str]]:
    """Split import roots into genuinely external ones and first-party prefixes.

    Returns (external_roots, first_party_prefixes, corroborated_prefixes) —
    the third being those with enough evidence to strip off a target, not only
    to classify (see `_LAYOUT_PREFIX_EVIDENCE`).

    A root is a *first-party prefix* when some import under it reaches a real
    node once its head is stripped. That happens whenever a project is
    ingested at a subdirectory: `cgis ingest ownima-backend/app` roots its
    nodes at `domains`, `api`, … while the code imports `from app.models
    import X`, so `app` looks like a third-party package. Every `app.*` FQN
    without a node then classified EXTERNAL, and the resolver minted a
    boundary node for a class of ours that does not exist — 2 461 confident
    edges to nothing on that codebase (#424).

    The discriminator is unambiguous in practice: measured there, `app` had
    3 346 of 4 885 import values reach a node while pydantic, sqlalchemy,
    grpc, fastapi and httpx had zero each. It asks whether the *import value*
    resolves, not whether some node's id ends with the root's name — the
    latter would match the library `grpc` against a project's own
    `api/dependencies/grpc/` package.
    """
    imports_by_root: dict[str, set[str]] = {}
    for import_map in file_imports.values():
        for val in import_map.values():
            if val and not val.startswith("."):
                imports_by_root.setdefault(val.split(".", maxsplit=1)[0], set()).add(val)

    external: set[str] = set()
    first_party: set[str] = set()
    corroborated: set[str] = set()
    for root, values in imports_by_root.items():
        resolving = sum(1 for v in values if _strips_to_a_node(v, node_ids))
        (first_party if resolving else external).add(root)
        if resolving >= _LAYOUT_PREFIX_EVIDENCE:
            corroborated.add(root)
    return external, first_party, corroborated


@dataclass(frozen=True)
class PythonIndexFacts:
    """What Python import maps and manifests say about the whole node set."""

    #: Second segments of `src`/`lib` layouts and first-party import prefixes (#424).
    internal_roots: frozenset[str]
    #: Import roots with enough evidence to strip off a target (#498 review).
    layout_prefixes: frozenset[str]
    #: Import roots no node of ours sits under.
    external_roots: frozenset[str]
    #: Declared dependencies that collide with an internal root (#495).
    dependency_roots: frozenset[str]
    #: `root.second` heads our own nodes have under those roots.
    dependency_heads: frozenset[str]


def index_facts(nodes: Mapping[str, Node], state: Mapping[str, object]) -> PythonIndexFacts:
    """Derive the facts from every node and the declared `python_dependencies`."""
    node_ids = set(nodes)
    local_roots: set[str] = set()
    layout_roots: set[str] = set()
    file_imports: dict[str, dict[str, str]] = {}
    for node in nodes.values():
        parts = node.id.split(".")
        local_roots.add(parts[0])
        if len(parts) > 1 and parts[0] in _LAYOUT_DIRS:
            layout_roots.add(parts[1])
        if node.type == NodeType.FILE:
            file_imports[os.path.normpath(node.file_path)] = node.metadata.get("import_map") or {}
    external, first_party, corroborated = _split_import_roots(file_imports, node_ids)
    declared = set(cast("list[str]", state.get("python_dependencies", [])))
    # Before first-party prefixes join: the set the old builder intersected (#495).
    colliding = (local_roots | layout_roots) & declared
    return PythonIndexFacts(
        internal_roots=frozenset(layout_roots | first_party),
        layout_prefixes=frozenset(corroborated),
        external_roots=frozenset(external),
        dependency_roots=frozenset(colliding),
        dependency_heads=_heads_under(colliding, node_ids),
    )


def classify(fqn: str, ctx: ClassifyContext) -> NodeNamespace:
    """The Python reading of a root: ours, a declared dependency, stdlib, or a known import."""
    facts = cast("PythonIndexFacts", ctx.facts)
    root = fqn.split(".", maxsplit=1)[0]
    if root in ctx.internal_roots:
        if _is_declared_dependency(fqn, root, facts, ctx.nodes):
            return NodeNamespace.EXTERNAL
        return NodeNamespace.INTERNAL
    if root == JS_BUILTINS_ROOT:
        return NodeNamespace.STDLIB
    if root in sys.stdlib_module_names or root in _BUILTINS:
        return NodeNamespace.STDLIB
    if root in facts.external_roots:
        return NodeNamespace.EXTERNAL
    return NodeNamespace.UNKNOWN
