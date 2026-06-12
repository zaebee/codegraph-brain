"""Compile a focal node's subgraph into an agent-facing context package (#19).

This is the machine-facing sibling of ``mermaid.py`` (human diagrams) and
``graph_json.py`` (joinable JSON): ``cgis context <fqn>`` feeds an LLM coding
agent a compact, XML-tagged prompt about the node it is about to edit — the
exact source, the enclosing class, who calls it (upstream ripple) and what it
calls (downstream dependencies).

The shape is deliberately token-lean. Adjacency is rendered as FQN + location
bullet lists rather than Mermaid (a visual format an LLM parses at several times
the token cost), and only the focal node's source is inlined — never whole
files. XML tags give the model unambiguous section boundaries, which mitigates
the "lost in the middle" failure mode on larger contexts.

This module is **pure**: it takes already-fetched nodes/edges and a pre-read
source string, so it can be unit-tested in isolation. Graph traversal and file
I/O live in ``context_service.py`` and ``snippet.py`` respectively.
"""

import re

from cgis.core.models import Node

_BACKTICK_RUN = re.compile(r"`+")
# Only the closing tags this module itself emits — so neutralising them in source
# defeats prompt-injection without corrupting unrelated markup (e.g. TS/JSX `</div>`).
_OWN_TAGS = ("source", "context", "class", "domain", "callers", "callees")
_OWN_CLOSING_TAG = re.compile(r"</(" + "|".join(_OWN_TAGS) + r")>")


def _escape_xml_attr(value: str) -> str:
    """Escape a value for safe inclusion in an XML attribute (``&`` first)."""
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _callers_note(depth: int) -> str:
    """Honest <callers> note: 'direct' only at depth 1, hop-bounded above it."""
    if depth <= 1:
        return "direct callers — who calls this (changes here ripple from here)"
    return f"callers within {depth} hops upstream (transitive ripple set)"


def _callees_note(depth: int) -> str:
    """Honest <callees> note: 'direct' only at depth 1, hop-bounded above it."""
    if depth <= 1:
        return "direct callees — what this calls"
    return f"callees within {depth} hops downstream (transitive execution flow)"


def _basename(file_path: str) -> str:
    """Return the file name portion of a (possibly slash-separated) path."""
    return file_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]


def _loc(node: Node) -> str:
    """Render a compact ``basename:line`` location for a node."""
    return f"{_basename(node.file_path)}:{node.start_line}"


def _fence(code: str) -> str:
    """Wrap ``code`` in a python code fence wide enough to survive inner backticks.

    Per CommonMark, a fenced block ends only on a backtick run at least as long
    as its opener. Choosing one more backtick than the longest run inside the
    source keeps embedded triple-backticks (or ``</source>``-style strings) from
    prematurely closing the block — the prompt-injection watch-out in #19.
    """
    longest = max((len(run) for run in _BACKTICK_RUN.findall(code)), default=0)
    fence = "`" * max(3, longest + 1)
    body = code if code.endswith("\n") else code + "\n"
    return f"{fence}python\n{body}{fence}"


def _caller_line(node: Node) -> str:
    """Bullet for a caller/callee: full FQN + type + location (joinable)."""
    return f"- {node.id} ({node.type.value}, {_loc(node)})"


def _sibling_line(node: Node) -> str:
    """Bullet for a class sibling: short name + type + location (compact)."""
    return f"- {node.name} ({node.type.value}, {_loc(node)})"


def _neutralize_closing_tags(code: str) -> str:
    """Escape only *this module's* closing tags in source so it can't close the prompt.

    The adaptive backtick fence stops a code block from closing early, but a file
    literally containing ``</source>``/``</context>`` could otherwise close the
    surrounding XML tag and inject instructions (#19 watch-out #2). We neutralise
    exactly the closing tags this module emits — leaving unrelated markup such as
    TS/JSX ``</div>``, return arrows (``->``) and generics verbatim for the agent.
    """
    return _OWN_CLOSING_TAG.sub(r"&lt;/\1>", code)


def _source_section(source: str) -> str:
    """Render the <source> tag, fencing real code (closing-tag-safe) or noting its absence."""
    if not source.strip():
        return "<source>(source unavailable)</source>"
    return f"<source>\n{_fence(_neutralize_closing_tags(source))}\n</source>"


def _class_section(class_node: Node | None, siblings: list[Node]) -> str:
    """Render the <class> tag with sibling members, or a standalone-function note."""
    if class_node is None:
        return "<class>none — module-level function</class>"
    members = "\n".join(_sibling_line(s) for s in siblings) or "(no other members)"
    name = _escape_xml_attr(class_node.id)
    file_attr = _escape_xml_attr(f"{class_node.file_path}:{class_node.start_line}")
    header = f'<class name="{name}" file="{file_attr}">'
    return f"{header}\n{members}\n</class>"


def _domain_section(focus: Node) -> str | None:
    """Render the L3 <domain> boundary block, or ``None`` when the node has no domain tags.

    Surfacing the focal node's architectural domain lets an agent respect
    boundary rules during refactoring (the two-layer-context insight from #19).
    Gated on ``focus.domains`` — the real semantic signal — so the structural
    ``ontology_class`` fallback (``"Function"``/``"Method"``/``"Class"``) that
    every node carries does not emit a meaningless boundary block.
    """
    if not focus.domains:
        return None
    ontology = _escape_xml_attr(focus.ontology_class or "-")
    domains = _escape_xml_attr(", ".join(focus.domains))
    return (
        f'<domain ontology_class="{ontology}" domains="{domains}">'
        "respect these architectural boundaries when refactoring"
        "</domain>"
    )


def _callers_section(callers: list[Node], depth: int) -> str:
    """Render the <callers> tag, with an explicit note when there are none."""
    body = "\n".join(_caller_line(c) for c in callers) or "none — no upstream callers"
    return f'<callers note="{_callers_note(depth)}">\n{body}\n</callers>'


def _callees_section(callees: list[Node], unresolved_callees: list[str], depth: int) -> str:
    """Render the <callees> tag: resolved deps plus flagged unresolved targets."""
    lines = [_caller_line(c) for c in callees]
    lines += [f"- {name} (unresolved)" for name in unresolved_callees]
    body = "\n".join(lines) or "none"
    return f'<callees note="{_callees_note(depth)}">\n{body}\n</callees>'


def compile_context(
    focus: Node,
    source: str,
    class_node: Node | None,
    siblings: list[Node],
    callers: list[Node],
    callees: list[Node],
    unresolved_callees: list[str],
    depth: int = 1,
) -> str:
    """Assemble the XML-tagged context package for ``focus``.

    ``source`` is the focal node's raw code (already read from disk, may be
    empty). ``class_node``/``siblings`` describe the enclosing class (``None``
    for a module-level function). ``callers``/``callees`` are the CALLS
    neighbours reached within ``depth`` hops; ``unresolved_callees`` are
    raw_call target names that never resolved to a node, listed so the agent
    knows the dependency exists but is external/dynamic. ``depth`` is carried
    only to label the caller/callee notes honestly — at depth 1 they are
    "direct", above it the note states the hop bound rather than claiming
    directness.
    """
    focal = _escape_xml_attr(focus.id)
    file_attr = _escape_xml_attr(f"{focus.file_path}:{focus.start_line}-{focus.end_line}")
    header = f'<context focal="{focal}" type="{focus.type.value}" file="{file_attr}">'
    sections = [
        _source_section(source),
        _class_section(class_node, siblings),
        _domain_section(focus),
        _callers_section(callers, depth),
        _callees_section(callees, unresolved_callees, depth),
    ]
    body = "\n\n".join(section for section in sections if section)
    return f"{header}\n\n{body}\n\n</context>"
