"""Import aliases a TypeScript project declares in `tsconfig.json` `compilerOptions.paths` (#508).

`import { Button } from "@components/ui/Button"` in `apps/web` means
`apps/web/components/ui/Button.tsx` when `apps/web/tsconfig.json` says
`"@components/*": ["components/*"]`. The extractor records the target dotted,
`@components.ui.Button`, and the resolver can map it onto the module only when told
what the alias stands for in the importing file's project.

That is per tsconfig, not global like the workspace packages (#506): a monorepo has
one tsconfig per app, and the same `@lib/*` can name different directories in two
of them. So the aliases are keyed by the directory of the `tsconfig.json` that
declares them, and a file uses the nearest one above it — the project it belongs
to. A project whose tsconfig declares no aliases is recorded too, with none: it
shadows the aliases of a project above it.

Aliases are inherited along `extends`, and a `paths` target is relative to the
effective `baseUrl` when one is set, otherwise to the directory of the config that
declared `paths` — TypeScript's own rule since 4.1.
"""

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import structlog

from cgis.extractors.base import BaseExtractor, ModuleNamer

logger = structlog.getLogger(__name__)

#: The config file whose directory is a TypeScript project.
TSCONFIG = "tsconfig.json"

#: The wildcard a `paths` pattern and its targets may carry, once.
_WILDCARD = "*"

#: A comment, or a string literal kept so that `"@/*"` is not read as a comment.
_JSONC_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
#: A comma closing an object or array, which JSONC allows and JSON does not.
_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)

#: How deep an `extends` chain may go before it is taken to be a cycle.
_MAX_EXTENDS_DEPTH = 16


def load_jsonc(text: str) -> object:
    """Parse a tsconfig: JSON with comments and trailing commas, as TypeScript reads it.

    String literals are matched first and kept, so a `/*` or `//` inside one —
    `"@/*"` is the common case — is never taken for a comment.
    """
    no_comments = _JSONC_TOKENS.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else "", text)
    no_commas = _TRAILING_COMMA.sub(lambda m: m.group(1) or m.group(0), no_comments)
    return json.loads(no_commas)


@dataclass(frozen=True)
class _Options:
    """The compiler options aliases depend on, after `extends` is applied."""

    paths: Mapping[str, object] | None = None
    #: Directory of the config that declared `paths`: their base without a `baseUrl`.
    paths_dir: Path | None = None
    base_url: Path | None = None

    def overlaid(self, other: "_Options") -> "_Options":
        """These options with `other`'s set ones taking precedence, as an extending config's do."""
        return _Options(
            paths=other.paths if other.paths is not None else self.paths,
            paths_dir=other.paths_dir if other.paths is not None else self.paths_dir,
            base_url=other.base_url if other.base_url is not None else self.base_url,
        )


class TsconfigPaths:
    """Collects `tsconfig.json` files during a walk and hands the resolver their aliases."""

    def __init__(self, workspace_root: Path, extractors: Mapping[str, BaseExtractor]) -> None:
        """Collect for `workspace_root`, naming directories with the TypeScript extractor's rule.

        The extractor is the one registered for `.ts`, chosen by extension so this
        module imports no language's extractor (#506). With none configured, or one
        that cannot name modules, every config is ignored.
        """
        self._root = workspace_root
        extractor = extractors.get(".ts") or extractors.get(".tsx")
        self._namer = extractor if isinstance(extractor, ModuleNamer) else None
        self._configs: set[Path] = set()

    def note(self, config: Path) -> None:
        """Record a `tsconfig.json` the walk passed; it is read in `aliases`."""
        self._configs |= {config}

    def aliases(
        self, package_directories: Mapping[str, Path] | None = None
    ) -> dict[str, dict[str, list[str]]]:
        """Project directory -> dotted alias pattern -> dotted target FQNs, in the order tried.

        `apps/web` -> `{"@lib.*": ["apps.web.lib.*"]}`. A pattern ending in `*`
        matches any import that starts with the rest of it, and the matched
        remainder replaces the `*` of each target. Targets are spelled as the node
        ids are, through the extractor's own `module_fqn`; one that leaves the
        repository or that cgis cannot spell is dropped. An unreadable config is
        skipped: one bad file must not stop the ingest.

        `package_directories` maps workspace package names to their directories, so
        `"extends": "@x/tsconfig/base.json"` finds a shared config that is not
        installed into `node_modules`.
        """
        if self._namer is None:
            return {}
        reader = _Reader(self._root, self._namer, dict(package_directories or {}))
        scopes: dict[str, dict[str, list[str]]] = {}
        for config in sorted(self._configs):
            try:
                directory = config.resolve().parent.relative_to(self._root).as_posix()
            except ValueError:
                continue
            options = _options(reader, config, depth=0)
            if options is None:
                continue
            scopes["" if directory == "." else directory] = _dotted(reader, options)
        return scopes


@dataclass(frozen=True)
class _Reader:
    """What reading one walk's configs needs: where the repository is and how it is named."""

    root: Path
    namer: ModuleNamer
    #: Workspace package names as written -> their directories.
    packages: Mapping[str, Path]


def _options(reader: _Reader, config: Path, depth: int) -> _Options | None:
    """The options `config` ends up with along its `extends` chain, or None if unreadable."""
    if depth > _MAX_EXTENDS_DEPTH:
        logger.warning("tsconfig extends chain too deep; stopping", path=str(config))
        return _Options()
    try:
        data = load_jsonc(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable tsconfig", path=str(config), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    options = _Options()
    for parent_ref in _extends_refs(data.get("extends")):
        parent = _locate(reader, parent_ref, config.parent)
        inherited = _options(reader, parent, depth + 1) if parent is not None else None
        if inherited is not None:
            options = options.overlaid(inherited)
    compiler = data.get("compilerOptions")
    if not isinstance(compiler, dict):
        return options
    own = _Options()
    if isinstance(compiler.get("paths"), dict):
        own = replace(own, paths=compiler["paths"], paths_dir=config.parent)
    if isinstance(compiler.get("baseUrl"), str):
        own = replace(own, base_url=config.parent / compiler["baseUrl"])
    return options.overlaid(own)


def _extends_refs(extends: object) -> list[str]:
    """The configs an `extends` value names: a string, or an array of them (TypeScript 5.0).

    Anything else is malformed and yields nothing, rather than being iterated —
    a dict would otherwise yield its keys as if they were configs.
    """
    if isinstance(extends, str):
        return [extends]
    if isinstance(extends, list):
        return [ref for ref in extends if isinstance(ref, str)]
    return []


def _locate(reader: _Reader, reference: str, directory: Path) -> Path | None:
    """The config file an `extends` value names, or None when it is not in the checkout.

    A relative or absolute path is taken as written, with `.json` added when the
    bare name does not exist. A package specifier is looked up among the
    workspace packages, then in `node_modules` up to the repository root; a bare
    package name means its `tsconfig.json`.
    """
    if reference.startswith((".", "/")):
        return _existing_json(directory / reference)
    scoped = reference.startswith("@")
    parts = reference.split("/")
    name = "/".join(parts[: 2 if scoped else 1])
    subpath = "/".join(parts[2 if scoped else 1 :]) or TSCONFIG
    bases = [reader.packages[name]] if name in reader.packages else []
    current = directory
    while True:
        bases.append(current / "node_modules" / name)
        if current == reader.root or current.parent == current:
            break
        current = current.parent
    for base in bases:
        found = _existing_json(base / subpath)
        if found is not None:
            return found
    return None


def _dotted(reader: _Reader, options: _Options) -> dict[str, list[str]]:
    """The aliases of one project, spelled as import targets and node ids are."""
    base = options.base_url or options.paths_dir
    if options.paths is None or base is None:
        return {}
    aliases: dict[str, list[str]] = {}
    for pattern, targets in options.paths.items():
        if pattern.count(_WILDCARD) > 1 or (
            _WILDCARD in pattern and not pattern.endswith(_WILDCARD)
        ):
            continue
        wildcard = pattern.endswith(_WILDCARD)
        spelled = [
            fqn
            for target in (targets if isinstance(targets, list) else [])
            if isinstance(target, str)
            for fqn in [_target_fqn(reader, base, target, wildcard)]
            if fqn is not None
        ]
        if spelled:
            aliases[pattern.replace("/", ".")] = spelled
    return aliases


def _target_fqn(reader: _Reader, base: Path, target: str, wildcard: bool) -> str | None:
    """The dotted FQN a `paths` target names, `*` kept, or None when it cannot be spelled.

    A wildcard target must end in `/*` (or be `*`): `lib/*.ts` would put the
    matched remainder before an extension, which a dotted FQN cannot express. A
    target that leaves the repository has no node to land on.
    """
    if wildcard != target.endswith(_WILDCARD) or target.count(_WILDCARD) > 1:
        return None
    if wildcard and target != _WILDCARD and not target.endswith("/" + _WILDCARD):
        return None
    try:
        relative = base.resolve().relative_to(reader.root).as_posix()
    except ValueError:
        return None
    path = posixpath.normpath(posixpath.join(relative, target.removesuffix(_WILDCARD)))
    # An absolute target replaces the base in the join, and the FQN helper would
    # then strip its leading `/` — `/opt/lib/*` into an in-repo `opt.lib.*`.
    if path == ".." or path.startswith(("../", "/")):
        return None
    if not wildcard:
        return None if path == "." else reader.namer.module_fqn(path)
    # A module inside the directory, then its last segment dropped: the directory's
    # own FQN with the source root applied, `""` when that strips it whole — where
    # `index.ts` would come back as a module named `index`.
    inside = posixpath.join(path, "_.ts") if path != "." else "_.ts"
    prefix = reader.namer.module_fqn(inside).rpartition(".")[0]
    return f"{prefix}.{_WILDCARD}" if prefix else _WILDCARD


def _existing_json(path: Path) -> Path | None:
    """`path` if it is a file, else `path.json` if that is, else None."""
    if path.is_file():
        return path
    with_suffix = path.with_name(path.name + ".json")
    return with_suffix if with_suffix.is_file() else None
