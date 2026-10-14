"""The Python pack's resolution rules (spec: Resolution; language-packs step 4)."""

from cgis.core.resolution import ResolutionRules, ResolutionView


def reconcile_import(fqn: str, _source_file: str | None, view: ResolutionView) -> str | None:
    """A Python import's node once corroborated layout prefixes are stripped (#319, #494)."""
    return view.strip_layout_prefixes(fqn)


RULES = ResolutionRules(reconcile_import=reconcile_import)
