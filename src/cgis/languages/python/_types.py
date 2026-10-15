"""Type-annotation resolution for the Python extractor."""

from collections.abc import Callable

from cgis.languages.python._syntax import file_path_to_module_fqn

#: Annotations that say "no static type". A receiver typed with one of these is
#: as unknown as an unannotated one, and is recorded the same way: not at all.
_OPAQUE_TYPES: frozenset[str] = frozenset({"typing.Any", "typing_extensions.Any"})


class TypeResolver:
    """Resolves Python type annotations to fully qualified names.

    Owns the type-string cleanup (stripping Optionals/Unions/generics) and the
    import-map lookup that turns a bare or module-prefixed type name into a FQN.
    """

    # `Annotated[T, ...]` unwraps to its first arg T (the type); the remaining
    # args are metadata (e.g. FastAPI `Depends(...)`, captured separately as a
    # DEPENDS_ON edge via the call-node path). See #194.
    _GENERIC_WRAPPERS: frozenset[str] = frozenset({"Optional", "Union", "Annotated"})

    def __init__(self, pick_source_root: Callable[[str], str | None]) -> None:
        """Store the per-file source-root picker used for FQN construction."""
        self._pick_source_root = pick_source_root

    def resolve_type_fqn(
        self,
        type_name: str,
        import_map: dict[str, str] | None,
        file_path: str,
    ) -> str | None:
        """Resolve a type name (possibly module-prefixed) to a FQN, or None for `Any`.

        `Any` declares that the value has no static type. Recording it as one made
        every call on such a value resolve to `typing.Any.<method>` — a stdlib
        target, so it was kept at confidence 1.0, and a confident wrong edge is
        invisible to the coverage note, which counts only unresolved calls (#636).
        The check is on the resolved FQN, so `t.Any` and `Any as A` are caught too.
        """
        fqn = self._lookup_type_fqn(type_name, import_map, file_path)
        return None if fqn in _OPAQUE_TYPES else fqn

    def _lookup_type_fqn(
        self,
        type_name: str,
        import_map: dict[str, str] | None,
        file_path: str,
    ) -> str:
        """The FQN a type name refers to: through the import map, else the file's module."""
        if import_map:
            if type_name in import_map:
                return import_map[type_name]
            if "." in type_name:
                module_part, _, rest = type_name.partition(".")
                if module_part in import_map:
                    return f"{import_map[module_part]}.{rest}"
        module = file_path_to_module_fqn(file_path, self._pick_source_root(file_path))
        return f"{module}.{type_name}"

    def clean_python_type_string(self, type_str: str) -> str:
        """
        Extract the main class name from type strings with Optionals/Unions.

        Examples:
            "SQLiteStore | None" -> "SQLiteStore"
            "Optional[SQLiteStore]" -> "SQLiteStore"
            "Union[SQLiteStore, None]" -> "SQLiteStore"
            "list[Node]" -> "list"
        """
        # Handle union types (PEP 604): "A | None" -> "A"
        if " | " in type_str:
            type_str = type_str.split(" | ", maxsplit=1)[0]
        # Handle generic wrappers: Optional[X] -> X, Union[X, Y] -> X, list[X] -> list
        if "[" in type_str:
            outer, _, inner = type_str.partition("[")
            # Support both `Optional[X]` and `typing.Optional[X]`
            outer_base = outer.strip().split(".")[-1]
            if outer_base in self._GENERIC_WRAPPERS:
                # Strip only the single outermost `]` to avoid mangling nested generics
                if inner.endswith("]"):
                    inner = inner[:-1]
                first_arg = self._first_top_level_arg(inner)
                return self.clean_python_type_string(first_arg)
            type_str = outer
        return type_str.strip()

    @staticmethod
    def _first_top_level_arg(args: str) -> str:
        """Return the first argument of a generic's arg list.

        Splits on the first comma at bracket depth 0 so nested generics keep
        their inner commas intact, e.g. "dict[str, int], Meta" -> "dict[str, int]"
        (a naive ``split(",")[0]`` would truncate to "dict[str").
        """
        depth = 0
        for idx, char in enumerate(args):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            elif char == "," and depth == 0:
                return args[:idx].strip()
        return args.strip()
