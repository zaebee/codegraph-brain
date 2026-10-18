"""The Python pack's resolution rules (spec: Resolution; language-packs step 4)."""

from cgis.core.resolution import ResolutionRules, ResolutionView
from cgis.languages.python.profile import INVOKERS


def reconcile_import(fqn: str, _source_file: str | None, view: ResolutionView) -> str | None:
    """A Python import's node once corroborated layout prefixes are stripped (#319, #494)."""
    return view.strip_layout_prefixes(fqn)


def annotated_alias(type_fqn: str, view: ResolutionView) -> str:
    """`T` when `type_fqn` is an `Annotated[T, Depends(...)]` DI alias, else `type_fqn`.

    A parameter typed `service: ServiceDep` holds a `Service`, but its recorded
    type is the alias, which has no methods — so `service.run()` stayed
    unresolved and impact stopped short of every FastAPI route injected that way
    (#550). Read for a parameter's calls and for `self.<attr>` alike.
    Moved from `cgis.resolver.symbols` in language-packs step 4: the Python
    pack's type normalizer, which the engine applies to every declared type.
    """
    alias_fqn = view.map_to_node_fqn(type_fqn) or type_fqn
    if not view.is_variable_node(alias_fqn):
        return type_fqn
    annotated: str | None = view.nodes[alias_fqn].metadata.get("annotated_type")
    if not annotated:
        return type_fqn
    # Recorded through the alias module's import map, so it may carry a layout
    # prefix (`app.domains.x.T` against a node `domains.x.T`).
    return view.map_to_node_fqn(annotated) or annotated


RULES = ResolutionRules(
    type_normalizers=(annotated_alias,), reconcile_import=reconcile_import, invokers=INVOKERS
)
