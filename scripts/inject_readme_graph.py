"""Inject a Mermaid diagram into README.md between CGIS graph comment anchors.

Reads docs/architecture/diagrams/pipeline_flow.mermaid and replaces the
content between <!-- START_CGIS_GRAPH --> and <!-- END_CGIS_GRAPH --> in
README.md. Idempotent — running multiple times produces identical output.
"""

from pathlib import Path

_README = Path("README.md")
_DIAGRAM = Path("docs/architecture/diagrams/pipeline_flow.mermaid")
_START = "<!-- START_CGIS_GRAPH -->"
_END = "<!-- END_CGIS_GRAPH -->"


def inject_graph() -> None:
    if not _README.exists():
        msg = f"README not found at {_README}"
        raise FileNotFoundError(msg)
    if not _DIAGRAM.exists():
        msg = f"Diagram not found at {_DIAGRAM}. Run: cgis trace ... -f mermaid > {_DIAGRAM}"
        raise FileNotFoundError(msg)

    mermaid = _DIAGRAM.read_text(encoding="utf-8").strip()
    block = f"{_START}\n```mermaid\n{mermaid}\n```\n{_END}"

    content = _README.read_text(encoding="utf-8")
    if _START not in content or _END not in content:
        msg = f"Anchor tags not found in {_README}. Add {_START!r} and {_END!r}."
        raise ValueError(msg)

    head, rest = content.split(_START, 1)
    _, tail = rest.split(_END, 1)
    _README.write_text("".join([head, block, tail]), encoding="utf-8")  # NOSONAR
    print(f"✅ Injected architecture graph into {_README}")


if __name__ == "__main__":
    inject_graph()
