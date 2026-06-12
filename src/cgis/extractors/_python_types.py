"""Type-annotation resolution for the Python extractor."""

from collections.abc import Callable

from cgis.extractors._python_ast import file_path_to_module_fqn


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
    ) -> str:
        """Resolve a type name (possibly module-prefixed) to a FQN."""
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
                first_arg = inner.split(",")[0].strip()
                return self.clean_python_type_string(first_arg)
            type_str = outer
        return type_str.strip()
