"""One definition of what generated code is (#432).

`cgis orphans` is correct about generated code — nothing constructs a betterproto
stub, and nobody deletes one either — so every such class dilutes a report whose
whole value is that a reader acts on it. Measured on `Ownima/owner-api` at
`b7d02fe6`, five of the six reported orphans were betterproto entities and the
sixth was a nested pydantic `Config`: the report had no actionable row left.

The decision lives in one function so "what is generated" has one answer, the
same way `is_test_path` owns "what is a test". `Node.is_generated` is its cached
result, stamped once per file by the pipeline, so a query filters in SQL without
re-reading source.

Only the **header** is scanned. A marker further down is prose: owner-api's
`domains/chat/schemas.py` explains in its docstring that a value is generated on
read, and scanning the whole file would hide its hand-written classes. Six of
owner-api's seven marker-bearing files declare it in the first four lines; the
seventh is that docstring.

The two markers are the conventions that generators actually emit — `@generated`
(the portable one) and a `do not edit` instruction. Anything narrower misses
protoc; anything broader starts matching prose.
"""

import re

# Scanned window. Four lines covers protoc/betterproto, openapi-generator and
# the `@generated` convention; five leaves room for a shebang or coding line
# above the marker without reaching into module docstrings.
_HEADER_LINES = 5

# Two markers, two casing rules, so they need two patterns — one combined regex
# with IGNORECASE would quietly make both case-insensitive.
#
# `@generated` is a machine token and the lowercase spelling is the convention,
# so it is matched exactly: any casing would let a header that writes
# "@Generated" in prose pass. (Java's `@Generated` annotation is a real
# spelling, but it sits on a declaration rather than a header line, and no Java
# extractor exists yet — one can add it with a test rather than inherit it.)
#
# `do not edit` is an English instruction and generators disagree: protoc shouts
# it, openapi-generator writes "Do not edit the class manually."
_GENERATED = re.compile(r"@generated")
_DO_NOT_EDIT = re.compile(r"do not edit", re.IGNORECASE)


def is_generated_source(code: str) -> bool:
    """Does this source declare itself machine-generated in its header?

    Used to keep generated classes out of `cgis orphans` by default: they are
    genuinely unreferenced, and reporting them is noise rather than a finding.
    `--include-generated` puts them back for the rare audit that wants them.
    """
    header = code.split("\n", maxsplit=_HEADER_LINES)[:_HEADER_LINES]
    return any(_GENERATED.search(line) or _DO_NOT_EDIT.search(line) for line in header)
