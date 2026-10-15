"""Which methods a call through an interface may run, in Go (#609, #642).

Go has no inheritance. Embedding a type promotes its methods into the outer type's
method set, but a call to the embedded method always runs that method: there is no
override, so embedding dispatches nothing (#642). What does dispatch is an interface.
A type satisfies an interface by having its methods, without saying so, and a call
on an interface value runs whichever type the value holds.

So each interface method gets one DISPATCHES edge to the method each satisfying type
would run: its own, or the one promoted from what it embeds. Impact walks DISPATCHES
upstream, which is how a caller that only ever sees the interface reaches the
concrete method.

Satisfaction is decided by method names alone. Signatures are not compared, and
neither is a value against a pointer receiver. That makes it an upper bound, like
class hierarchy analysis, hence the same lower confidence. Interfaces outside the
graph (`io.Writer`) have no methods here and are left alone. An interface that embeds
one is matched on the methods the graph can see, which over-matches when those are few
(`interface{ io.Reader; Close() }` is "satisfied" by any type with `Close`). On
hashicorp/raft, skipping such interfaces lost 9 true edges (`SnapshotSink`,
`StreamLayer`) and prevented no false one, so they stay.

Left alone: an interface with no methods (`any`), which every type satisfies, and
anything declared in test code (a fake implementing a production interface is not a
caller's concern; impact leaves tests out).

A struct may embed an interface: it then holds the interface's methods as its own.
Its "implementation" of an interface method is a declaration that re-dispatches, so
an edge to another interface's method is kept (the chain continues from there). A
method dispatching to itself is not an edge.
"""

from collections.abc import Iterable, Mapping, Sequence

from cgis.core.models import Edge, EdgeType, Node

#: As class hierarchy analysis (`cgis.resolver.symbols.DISPATCH_CONFIDENCE`): a
#: satisfying type is a type the interface value *may* hold, not one it does.
DISPATCH_CONFIDENCE = 0.5


def interface_dispatch(
    methods: Sequence[Node], classes: Sequence[Node], inheritance: Mapping[str, list[str]]
) -> list[Edge]:
    """One DISPATCHES edge from each interface method to each implementation of it."""
    declared = _methods_by_owner(m for m in methods if not m.is_test)
    interfaces = {c.id for c in classes if c.metadata.get("interface") and not c.is_test}
    embeds_interfaces = {
        i: [e for e in inheritance.get(i, []) if e in interfaces] for i in interfaces
    }
    method_sets = {
        c.id: _method_set(c.id, declared, inheritance)
        for c in classes
        if c.id not in interfaces and not c.is_test
    }
    holders: dict[str, list[str]] = {}
    for type_id, method_set in sorted(method_sets.items()):
        for name in method_set:
            holders.setdefault(name, []).append(type_id)
    edges: dict[str, Edge] = {}
    for interface in sorted(interfaces):
        needed = _method_set(interface, declared, embeds_interfaces)
        if not needed:
            continue
        # Only a type with the interface's rarest method can satisfy it.
        rarest = min(needed, key=lambda name: (len(holders.get(name, [])), name))
        for type_id in holders.get(rarest, []):
            method_set = method_sets[type_id]
            if all(name in method_set for name in needed):
                for name, declaration in sorted(needed.items()):
                    if method_set[name].id == declaration.id:
                        continue
                    edge = _edge(declaration, method_set[name], type_id, interface)
                    edges[edge.id] = edge
    return list(edges.values())


def _edge(declaration: Node, implementation: Node, type_id: str, interface: str) -> Edge:
    """DISPATCHES from an interface method to the method a satisfying type runs."""
    edge_id = f"dispatch:{declaration.id}->{implementation.id}"
    return Edge(
        id=edge_id,
        source=declaration.id,
        target=implementation.id,
        type=EdgeType.DISPATCHES,
        confidence=DISPATCH_CONFIDENCE,
        context=f"{type_id} satisfies {interface}",
        file_path=implementation.file_path,
        line_number=implementation.start_line,
    )


def _methods_by_owner(methods: Iterable[Node]) -> dict[str, dict[str, Node]]:
    """Methods keyed by the type that declares them, then by name."""
    owners: dict[str, dict[str, Node]] = {}
    for method in methods:
        owner, _, name = method.id.rpartition(".")
        owners.setdefault(owner, {})[name] = method
    return owners


def _method_set(
    type_id: str, declared: Mapping[str, Mapping[str, Node]], inheritance: Mapping[str, list[str]]
) -> dict[str, Node]:
    """A type's methods, own and promoted, each as the node that defines it.

    Breadth first through what the type embeds: a shallower method shadows a deeper
    one of the same name, as in Go. Two at the same depth are ambiguous in Go, and
    calling one does not compile. The first in source order is kept, so the method
    still counts toward satisfaction.
    """
    found: dict[str, Node] = {}
    seen = {type_id}
    level = [type_id]
    while level:
        next_level: list[str] = []
        for current in level:
            for name, method in declared.get(current, {}).items():
                found.setdefault(name, method)
            for embedded in inheritance.get(current, []):
                if embedded not in seen:
                    seen.add(embedded)
                    next_level.append(embedded)
        level = next_level
    return found
