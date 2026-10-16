"""Which Go files the default build reads: linux/amd64, as `go build`, gopls and go/types do (#657).

A Go package often holds several files that define the same function for
different builds: `process_collector_windows.go`, `..._other.go` with
`//go:build !windows && !js`, `foo_go117.go` with `//go:build go1.17`. Reading
them all gave one node for several bodies, its local types taken from whichever
file came last, so the other bodies' calls resolved against types that were not
theirs. The graph is built for one configuration instead, the one go/types loads
by default; a file outside it keeps a FILE node that says why, so the gap is
named rather than silent.

The build is linux/amd64 with cgo and the `gc` toolchain, and every Go release
tag up to `_LATEST_GO_MINOR`: `//go:build !go1.17` describes code for toolchains
older than any current one.
"""

import re

GOOS = "linux"
GOARCH = "amd64"
#: Release tags `go1.1` … `go1.<this>` hold, as they do for a current toolchain.
_LATEST_GO_MINOR = 26
_TAGS = frozenset(
    {
        GOOS,
        GOARCH,
        "unix",
        "gc",
        "cgo",
        *(f"go1.{minor}" for minor in range(1, _LATEST_GO_MINOR + 1)),
    }
)
#: The values `go/build` reads from a file name's last one or two `_` segments.
_KNOWN_OS = frozenset(
    [
        "aix",
        "android",
        "darwin",
        "dragonfly",
        "freebsd",
        "hurd",
        "illumos",
        "ios",
        "js",
        "linux",
        "nacl",
        "netbsd",
        "openbsd",
        "plan9",
        "solaris",
        "wasip1",
        "windows",
        "zos",
    ]
)
_KNOWN_ARCH = frozenset(
    [
        "386",
        "amd64",
        "amd64p32",
        "arm",
        "armbe",
        "arm64",
        "arm64be",
        "loong64",
        "mips",
        "mipsle",
        "mips64",
        "mips64le",
        "mips64p32",
        "mips64p32le",
        "ppc",
        "ppc64",
        "ppc64le",
        "riscv",
        "riscv64",
        "s390",
        "s390x",
        "sparc",
        "sparc64",
        "wasm",
    ]
)
_GO_BUILD = re.compile(r"^//go:build\s+(.+?)\s*$")
_PLUS_BUILD = re.compile(r"^//\s*\+build\s+(.+?)\s*$")
_TOKEN = re.compile(r"\s*(\|\||&&|!|\(|\)|[^\s|&!()]+)")


def excluded(code: str, file_name: str) -> str | None:
    """Why the default build leaves this file out, or None when it reads it."""
    by_name = _name_constraint(file_name)
    if by_name is not None:
        return f"file name: {by_name}"
    constraint = build_constraint(code)
    if constraint is not None and not satisfied(constraint):
        return constraint
    return None


def build_constraint(code: str) -> str | None:
    """The file's `//go:build` expression; else its `// +build` lines as one; else None.

    Only lines above the package clause count, as for the go command.
    """
    plus: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("package ") or stripped == "package":
            break
        match = _GO_BUILD.match(stripped)
        if match:
            return match.group(1)
        plus_match = _PLUS_BUILD.match(stripped)
        if plus_match:
            plus.append(_plus_line(plus_match.group(1)))
    return " && ".join(plus) if plus else None


def _plus_line(options: str) -> str:
    """`// +build a,!b c` is `(a && !b) || c`: spaces are OR, commas AND."""
    terms = [" && ".join(option.split(",")) for option in options.split()]
    return f"({' || '.join(terms)})"


def satisfied(expression: str) -> bool:
    """Whether a `//go:build` expression holds for the default build.

    Unknown or malformed syntax holds nothing: a file the build cannot place is
    left out and named, rather than read as part of every build.
    """
    tokens = [t for t in _TOKEN.findall(expression) if t]
    try:
        value, rest = _or(tokens)
    except IndexError:
        return False
    return value and not rest


def _or(tokens: list[str]) -> tuple[bool, list[str]]:
    value, rest = _and(tokens)
    while rest and rest[0] == "||":
        right, rest = _and(rest[1:])
        value = value or right
    return value, rest


def _and(tokens: list[str]) -> tuple[bool, list[str]]:
    value, rest = _not(tokens)
    while rest and rest[0] == "&&":
        right, rest = _not(rest[1:])
        value = value and right
    return value, rest


def _not(tokens: list[str]) -> tuple[bool, list[str]]:
    head = tokens[0]
    if head == "!":
        value, rest = _not(tokens[1:])
        return not value, rest
    if head == "(":
        value, rest = _or(tokens[1:])
        if not rest or rest[0] != ")":
            raise IndexError(head)
        return value, rest[1:]
    if head in ("||", "&&", ")"):
        raise IndexError(head)
    return head in _TAGS, tokens[1:]


def _name_constraint(file_name: str) -> str | None:
    """The `_GOOS`, `_GOARCH` or `_GOOS_GOARCH` suffix that rules a file out (`go/build`'s reading).

    The part before the first `_` never counts (`windows.go` is not constrained),
    and a `_test` suffix is skipped first (`x_windows_test.go` is).
    """
    stem = file_name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".go").removesuffix("_test")
    parts = stem.split("_")[1:]
    if len(parts) >= 2 and parts[-2] in _KNOWN_OS and parts[-1] in _KNOWN_ARCH:
        wanted = f"{parts[-2]}_{parts[-1]}"
        return None if (parts[-2], parts[-1]) == (GOOS, GOARCH) else wanted
    if parts and parts[-1] in _KNOWN_OS:
        return None if parts[-1] == GOOS else parts[-1]
    if parts and parts[-1] in _KNOWN_ARCH:
        return None if parts[-1] == GOARCH else parts[-1]
    return None
