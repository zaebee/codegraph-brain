"""Import-statement handling for the Python extractor.

Walks ``import`` / ``import_from`` AST nodes, builds the per-file ``import_map``
(local name -> target FQN) and emits IMPORTS / IMPORTS_SYMBOL edges.
"""

from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, EdgeType
from cgis.extractors._python_ast import resolve_relative_module


def _has_wildcard(node: BaseNode) -> bool:
    """True when this `from X import ...` is a star import."""
    return any(child.type == "wildcard_import" for child in node.children)


#: What a `TYPE_CHECKING` guard is spelled as: the bare name, or through the module.
_TYPE_CHECKING_NAMES = frozenset({"TYPE_CHECKING", "typing.TYPE_CHECKING"})


def _inside_type_checking(node: BaseNode, code_bytes: bytes) -> bool:
    """True when this statement sits inside the body of an `if TYPE_CHECKING:` block.

    Walks up rather than down: the guard may be nested (inside a class body, or in
    a second `if`), and the import handler only ever sees the import node itself.
    The condition is read verbatim, so `if TYPE_CHECKING:` and
    `if typing.TYPE_CHECKING:` both count while `if not TYPE_CHECKING:` and
    `if TYPE_CHECKING and sys.version_info >= (3, 12):` do not — a runtime edge too
    many is the safe direction here.

    **Which branch matters.** `else_clause` and `elif_clause` are children of the
    same `if_statement`, so testing the condition alone marked the runtime fallback
    of `if TYPE_CHECKING: … else: <shim>` as type-only, dropping a real dependency
    from cycle detection (#501 review). The import must sit in the consequence.
    """
    child = node
    current = node.parent
    while current is not None:
        if current.type == "if_statement" and _is_type_checking_branch(current, child, code_bytes):
            return True
        child = current
        current = current.parent
    return False


def _is_type_checking_branch(statement: BaseNode, child: BaseNode, code_bytes: bytes) -> bool:
    """True when `child` is the consequence of a TYPE_CHECKING `if`."""
    condition = statement.child_by_field_name("condition")
    consequence = statement.child_by_field_name("consequence")
    if condition is None or consequence is None or consequence.id != child.id:
        return False
    text = code_bytes[condition.start_byte : condition.end_byte].decode("utf-8")
    return text.strip().strip("()").strip() in _TYPE_CHECKING_NAMES


class ImportHandler:
    """Extracts import edges and populates the import map from import AST nodes."""

    def handle(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        import_map: dict[str, str] | None,
        module_fqn: str | None,
        edges: list[Edge],
        star_imports: list[str] | None = None,
    ) -> None:
        """Dispatch import or import-from AST nodes to the appropriate handler."""
        if import_map is None or module_fqn is None:
            return
        before = len(edges)
        if node.type == "import_statement":
            self._process_import_statement(
                node, code_bytes, module_fqn, file_path, import_map, edges
            )
        else:
            self._process_import_from_statement(
                node, code_bytes, module_fqn, file_path, import_map, edges, star_imports
            )
        if _inside_type_checking(node, code_bytes):
            # Marked after the fact rather than threaded through every emitter: the
            # guard is a property of where the statement sits, not of what it says,
            # and the two handlers build five kinds of edge between them (#499).
            #
            # Except where the same module is *also* imported at runtime: both edges
            # carry the same id, so `INSERT OR REPLACE` keeps whichever came last,
            # and marking the guarded copy made a real runtime dependency vanish
            # from cycle detection — a false negative in the very query this exists
            # to correct (#501 review). Imported both ways means runtime.
            runtime_ids = {edge.id for edge in edges[:before] if not edge.type_only}
            edges[before:] = [
                edge if edge.id in runtime_ids else edge.model_copy(update={"type_only": True})
                for edge in edges[before:]
            ]

    def _process_import_statement(
        self,
        node: BaseNode,
        code_bytes: bytes,
        module_fqn: str,
        file_path: str,
        import_map: dict[str, str],
        edges: list[Edge],
    ) -> None:
        """Handle `import X` and `import X as Y` statements."""
        for child in node.children:
            if child.type == "dotted_name":
                module_str = code_bytes[child.start_byte : child.end_byte].decode("utf-8")
                local_name = module_str.split(".")[0]
                import_map[local_name] = local_name
                edges.append(
                    Edge(
                        id=f"{module_fqn}:imports:{module_str}",
                        type=EdgeType.IMPORTS,
                        source=module_fqn,
                        target=module_str,
                        confidence=1.0,
                        file_path=file_path,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    module_str = code_bytes[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8"
                    )
                    alias = code_bytes[alias_node.start_byte : alias_node.end_byte].decode("utf-8")
                    import_map[alias] = module_str
                    edges.append(
                        Edge(
                            id=f"{module_fqn}:imports:{module_str}",
                            type=EdgeType.IMPORTS,
                            source=module_fqn,
                            target=module_str,
                            confidence=1.0,
                            file_path=file_path,
                        )
                    )

    def _parse_relative_import(self, node: BaseNode, code_bytes: bytes) -> tuple[int, str]:
        """Return (leading_dots, raw_module_str) from a relative_import node."""
        leading_dots = 0
        raw_module_str = ""
        for sub in node.children:
            if sub.type == "import_prefix":
                leading_dots = sub.end_byte - sub.start_byte
            elif sub.type == "dotted_name":
                raw_module_str = code_bytes[sub.start_byte : sub.end_byte].decode("utf-8")
        return leading_dots, raw_module_str

    def _collect_imported_symbols(
        self, children: list[BaseNode], code_bytes: bytes
    ) -> list[tuple[str, str]]:
        """Collect (local_name, qualified_symbol) pairs from a list of sibling nodes.

        Flattens parenthesized imports (`import_list` node) transparently.
        """
        items: list[BaseNode] = []
        for child in children:
            if child.type == "import_list":
                items.extend(child.children)
            else:
                items.append(child)
        symbols: list[tuple[str, str]] = []
        for child in items:
            if child.type in ("dotted_name", "identifier"):
                sym = code_bytes[child.start_byte : child.end_byte].decode("utf-8")
                symbols.append((sym.split(".")[-1], sym))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    sym = code_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                    alias = code_bytes[alias_node.start_byte : alias_node.end_byte].decode("utf-8")
                    symbols.append((alias, sym))
            # wildcard_import, punctuation → skip
        return symbols

    def _process_import_from_statement(
        self,
        node: BaseNode,
        code_bytes: bytes,
        module_fqn: str,
        file_path: str,
        import_map: dict[str, str],
        edges: list[Edge],
        star_imports: list[str] | None = None,
    ) -> None:
        """Handle `from X import Y [as Z]`, `from X import *`, and relative imports."""
        leading_dots = 0
        raw_module_str = ""
        past_import_kw = False
        import_symbol_node: BaseNode | None = None

        for child in node.children:
            if child.type == "relative_import":
                leading_dots, raw_module_str = self._parse_relative_import(child, code_bytes)
            elif child.type == "dotted_name" and not past_import_kw:
                raw_module_str = code_bytes[child.start_byte : child.end_byte].decode("utf-8")
            elif child.type == "import":
                past_import_kw = True
                import_symbol_node = child
            # imported symbols are siblings after the 'import' keyword — collected below

        # Collect all sibling nodes that come after the 'import' keyword
        symbols: list[tuple[str, str]] = []
        if import_symbol_node is not None:
            idx = node.children.index(import_symbol_node)
            symbols = self._collect_imported_symbols(node.children[idx + 1 :], code_bytes)

        norm_path = file_path.replace("\\", "/")
        is_package = norm_path == "__init__.py" or norm_path.endswith("/__init__.py")
        base_module = (
            resolve_relative_module(module_fqn, leading_dots, raw_module_str, is_package=is_package)
            if leading_dots > 0
            else raw_module_str
        )

        # `from X import *` names nothing here — the exporting module's own symbols
        # are the names, and only the index knows them. Record the module and let
        # IndexBuilder expand it (#417).
        if star_imports is not None and base_module and _has_wildcard(node):
            star_imports.append(base_module)

        for local_name, sym in symbols:
            target_fqn = f"{base_module}.{sym}" if base_module else sym
            import_map[local_name] = target_fqn
            # Symbol-level import edge (#161 slice 2): raw_import: candidates are
            # resolved to an existing node by the ResolverEngine or DROPPED —
            # they never leak into output (spec §2.2). Literal prefix mirrors
            # the raw_dep:/raw_call: convention used elsewhere in this file.
            edges.append(
                Edge(
                    id=f"{module_fqn}:imports_symbol:{target_fqn}",
                    type=EdgeType.IMPORTS_SYMBOL,
                    source=module_fqn,
                    target=f"raw_import:{target_fqn}",
                    confidence=0.1,
                    file_path=file_path,
                )
            )

        if base_module:
            edges.append(
                Edge(
                    id=f"{module_fqn}:imports:{base_module}",
                    type=EdgeType.IMPORTS,
                    source=module_fqn,
                    target=base_module,
                    confidence=1.0,
                    file_path=file_path,
                )
            )
