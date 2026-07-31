"""Generate the cgis GitHub App avatar.

Ships `v3-lit-path` as `cgis-app-avatar.png`; the other two are kept so the
alternatives are on the record rather than lost in a chat log.

The motif comes from the domain rather than decoration: cgis's drift alphabet is
built on directed triads (`_TRICODES` / `TRIAD_ORDER`), so the glyphs are actual
triad shapes — 021D (diverging) and the layered_dag ideal.

No arrowheads. SVG markers scale by stroke-width by default (a 4.5-unit head at
stroke 9 renders as a 40-unit blob), and even sized correctly an arrowhead is
mush at the 40px a bot avatar occupies in a PR thread. Hierarchy is carried by
node size and colour instead.

Rendered at 400px for upload and 40px to check legibility at comment size.
"""

import pathlib

import cairosvg

BG = "#0d1526"
EDGE = "#64748b"
CYAN = "#22d3ee"
VIOLET = "#a78bfa"
AMBER = "#fbbf24"

HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">'
    f'<rect width="200" height="200" rx="44" fill="{BG}"/>'
)
TAIL = "</svg>"


def edge(
    p1: tuple[float, float], p2: tuple[float, float], *, colour: str = EDGE, w: float = 8
) -> str:
    """A graph edge, trimmed at both ends so it meets the node rims cleanly."""
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    pad = 19
    return (
        f'<line x1="{x1 + ux * pad:.1f}" y1="{y1 + uy * pad:.1f}" '
        f'x2="{x2 - ux * pad:.1f}" y2="{y2 - uy * pad:.1f}" '
        f'stroke="{colour}" stroke-width="{w}" stroke-linecap="round"/>'
    )


def node(p: tuple[float, float], colour: str, r: float = 18) -> str:
    """A graph node."""
    return f'<circle cx="{p[0]}" cy="{p[1]}" r="{r}" fill="{colour}"/>'


TOP, LEFT, RIGHT, BOTTOM = (100, 42), (44, 100), (156, 100), (100, 158)

# 1. layered_dag — diverge at the top, converge at the bottom.
v1 = (
    HEAD
    + edge(TOP, LEFT)
    + edge(TOP, RIGHT)
    + edge(LEFT, BOTTOM)
    + edge(RIGHT, BOTTOM)
    + node(TOP, CYAN)
    + node(LEFT, VIOLET)
    + node(RIGHT, VIOLET)
    + node(BOTTOM, CYAN)
    + TAIL
)

# 2. 021D on its own — fewest strokes, best legibility at 40px.
T2, BL2, BR2 = (100, 54), (48, 144), (152, 144)
v2 = (
    HEAD
    + edge(T2, BL2, w=9)
    + edge(T2, BR2, w=9)
    + node(T2, CYAN, 21)
    + node(BL2, VIOLET, 21)
    + node(BR2, VIOLET, 21)
    + TAIL
)

# 3. layered_dag with one path lit — structure, plus the analysis laid over it.
v3 = (
    HEAD
    + edge(TOP, RIGHT)
    + edge(RIGHT, BOTTOM)
    + edge(TOP, LEFT, colour=AMBER, w=9)
    + edge(LEFT, BOTTOM, colour=AMBER, w=9)
    + node(TOP, CYAN)
    + node(LEFT, AMBER)
    + node(RIGHT, VIOLET)
    + node(BOTTOM, CYAN)
    + TAIL
)

HERE = pathlib.Path(__file__).parent
# Variants land in a gitignored scratch dir; only the chosen one is versioned,
# so re-running this never litters docs/assets.
VARIANTS = HERE / "_variants"
VARIANTS.mkdir(parents=True, exist_ok=True)

CHOSEN = "v3-lit-path"

for name, svg in (("v1-layered-dag", v1), ("v2-fanout", v2), ("v3-lit-path", v3)):
    (VARIANTS / f"{name}.svg").write_text(svg)
    for px in (400, 40):
        cairosvg.svg2png(
            bytestring=svg.encode(),
            write_to=str(VARIANTS / f"{name}-{px}.png"),
            output_width=px,
            output_height=px,
        )
    if name == CHOSEN:
        (HERE / "cgis-app-avatar.svg").write_text(svg)
        cairosvg.svg2png(
            bytestring=svg.encode(),
            write_to=str(HERE / "cgis-app-avatar.png"),
            output_width=400,
            output_height=400,
        )
    print(f"rendered {name}" + ("  <- shipped as cgis-app-avatar.*" if name == CHOSEN else ""))
