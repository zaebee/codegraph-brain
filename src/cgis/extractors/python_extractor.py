"""Implements Python Extractor."""

from typing import Any

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from tree_sitter import Node as BaseNode

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.base import BaseExtractor

_ABC_NAMES: frozenset[str] = frozenset({"ABC", "ABCMeta"})


def _resolve_relative_module(module_fqn: str, leading_dots: int, relative_path: str) -> str:
    """Resolve a relative import to an absolute FQN.

    Args:
        module_fqn: FQN of the current file, e.g. "src.cgis.extractors.python_extractor"
        leading_dots: number of leading dots in the import statement
        relative_path: module path after the dots, e.g. "core" for "from ..core import x"

    Returns "src.cgis.core" for leading_dots=2, relative_path="core" in the example above.
    """
    segments = module_fqn.split(".")
    trim = min(leading_dots, len(segments))
    base = ".".join(segments[:-trim]) if trim < len(segments) else ""
    if relative_path:
        return f"{base}.{relative_path}" if base else relative_path
    return base


def file_path_to_module_fqn(file_path: str, source_root: str | None = None) -> str:
    """Convert a file path to a dot-separated module namespace.

    Examples:
        src/cgis/pipeline.py  -> src.cgis.pipeline
        src/cgis/__init__.py  -> src.cgis
        /abs/path/mod.py      -> abs.path.mod
        C:\\path\\to\\mod.py  -> path.to.mod

        With source_root="src":
        src/cgis/pipeline.py  -> cgis.pipeline
    """
    clean = file_path
    # Strip Windows drive letter (e.g. "C:") before normalising slashes
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.replace("\\", "/").lstrip("/")
    if source_root:
        # removeprefix("./") so CI-style roots ("cgis ingest ./src") strip too
        sr = source_root.replace("\\", "/").removeprefix("./").strip("/") + "/"
        if clean.startswith(sr):
            clean = clean[len(sr) :]
    if clean.endswith(".py"):
        clean = clean[:-3]
    if clean.endswith("/__init__"):
        clean = clean[:-9]
    return clean.replace("/", ".")


class PythonExtractor(BaseExtractor):
    """
    A concrete extractor for Python source code using tree-sitter.
    """

    LANG: str = "python"

    def __init__(self, source_roots: list[str] | None = None) -> None:
        """Initialise the tree-sitter Python parser."""
        super().__init__(source_roots=source_roots)
        self._parser = Parser(Language(tspython.language()))

    def parse(self, code: str, file_path: str) -> tuple[list[Node], list[Edge]]:
        """
        Extracts structural nodes and edges (Functions, Classes, Imports).
        """
        code_bytes = code.encode("utf8")
        tree = self._parser.parse(code_bytes)
        root_node: BaseNode = tree.root_node

        nodes: list[Node] = []
        edges: list[Edge] = []
        import_map: dict[str, str] = {}
        module_fqn = file_path_to_module_fqn(file_path, self._pick_source_root(file_path))
        local_types_acc: dict[str, dict[str, str]] = {}

        self._walk(
            root_node,
            code_bytes,
            file_path,
            nodes,
            edges,
            import_map=import_map,
            module_fqn=module_fqn,
            local_types_acc=local_types_acc,
        )

        # Apply accumulated local types from assignments and param annotations
        nodes_by_id = {n.id: i for i, n in enumerate(nodes)}
        for func_id, lt in local_types_acc.items():
            if func_id in nodes_by_id:
                i = nodes_by_id[func_id]
                nodes[i] = nodes[i].model_copy(
                    update={"metadata": {**nodes[i].metadata, "local_types": lt}}
                )

        file_node = Node(
            id=module_fqn,
            type=NodeType.FILE,
            name=file_path.rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1],
            file_path=file_path,
            start_line=1,
            end_line=root_node.end_point.row + 1,
            metadata={"import_map": import_map},
        )
        nodes.insert(0, file_node)

        return nodes, edges

    def _get_fqn_prefix(self, node: BaseNode, code_bytes: bytes) -> str | None:
        """Traverse up to find class and function names, returning them joined by dots."""
        parts = []
        curr = node.parent
        extract_types = ("class_definition", "function_definition", "async_function_definition")
        while curr:
            if curr.type in extract_types:
                name_node = curr.child_by_field_name("name")
                parts.append(self._extract_node_name(name_node, code_bytes))
            curr = curr.parent
        return ".".join(reversed(parts)) if parts else None

    def _get_id(self, node: BaseNode, code_bytes: bytes, file_path: str) -> str:
        """Generate a fully qualified function/method ID including class/function context."""
        name = node.child_by_field_name("name")
        node_name = self._extract_node_name(name, code_bytes)
        prefix = self._get_fqn_prefix(node, code_bytes)
        module = file_path_to_module_fqn(file_path, self._pick_source_root(file_path))
        if not module:
            return f"{prefix}.{node_name}" if prefix else node_name
        return f"{module}.{prefix}.{node_name}" if prefix else f"{module}.{node_name}"

    def _walk(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        current_func_node: Node | None = None,
        import_map: dict[str, str] | None = None,
        module_fqn: str | None = None,
        local_types_acc: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """
        Recursive AST walker that extracts nodes and edges in a single pass.
        """
        if node.type in ("import_statement", "import_from_statement"):
            self._handle_import_node(node, code_bytes, file_path, import_map, module_fqn, edges)
            return  # never recurse into import nodes

        if node.type == "decorated_definition":
            self._handle_decorated_definition(
                node,
                code_bytes,
                file_path,
                nodes,
                edges,
                import_map,
                module_fqn,
                local_types_acc,
            )
            return  # prevent double-processing the inner definition

        next_func_node = current_func_node
        if node.type in ("function_definition", "async_function_definition"):
            next_func_node = self._process_function_node(
                node, code_bytes, file_path, nodes, edges, module_fqn or ""
            )
        elif node.type == "class_definition":
            self._process_class_node(node, code_bytes, file_path, nodes, edges, module_fqn or "")
            next_func_node = None
        elif node.type == "call" and current_func_node:
            self._process_call_node(node, code_bytes, file_path, current_func_node.id, edges)
        elif node.type == "assignment" and current_func_node and local_types_acc is not None:
            self._collect_assignment_type(
                node, code_bytes, import_map, current_func_node, local_types_acc
            )
        elif (
            node.type in ("typed_parameter", "typed_default_parameter")
            and current_func_node
            and local_types_acc is not None
        ):
            self._collect_param_type(
                node, code_bytes, import_map, current_func_node, local_types_acc
            )

        for child in node.children:
            self._walk(
                child,
                code_bytes,
                file_path,
                nodes,
                edges,
                next_func_node,
                import_map=import_map,
                module_fqn=module_fqn,
                local_types_acc=local_types_acc,
            )

    def _handle_import_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        import_map: dict[str, str] | None,
        module_fqn: str | None,
        edges: list[Edge],
    ) -> None:
        """Dispatch import or import-from AST nodes to the appropriate handler."""
        if import_map is None or module_fqn is None:
            return
        if node.type == "import_statement":
            self._process_import_statement(
                node, code_bytes, module_fqn, file_path, import_map, edges
            )
        else:
            self._process_import_from_statement(
                node, code_bytes, module_fqn, file_path, import_map, edges
            )

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
    ) -> None:
        """Handle `from X import Y [as Z]` and relative imports."""
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

        base_module = (
            _resolve_relative_module(module_fqn, leading_dots, raw_module_str)
            if leading_dots > 0
            else raw_module_str
        )

        for local_name, sym in symbols:
            import_map[local_name] = f"{base_module}.{sym}" if base_module else sym

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

    def _handle_decorated_definition(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        import_map: dict[str, str] | None,
        module_fqn: str | None,
        local_types_acc: dict[str, dict[str, str]] | None,
    ) -> None:
        """Process a decorated function or class definition, forwarding decorator names."""
        raw_decorators = self._extract_decorator_names(node, code_bytes)
        for child in node.children:
            if child.type in ("function_definition", "async_function_definition"):
                inner = self._process_function_node(
                    child,
                    code_bytes,
                    file_path,
                    nodes,
                    edges,
                    module_fqn or "",
                    decorators=raw_decorators,
                )
                for grandchild in child.children:
                    self._walk(
                        grandchild,
                        code_bytes,
                        file_path,
                        nodes,
                        edges,
                        inner,
                        import_map=import_map,
                        module_fqn=module_fqn,
                        local_types_acc=local_types_acc,
                    )
            elif child.type == "class_definition":
                self._process_class_node(
                    child,
                    code_bytes,
                    file_path,
                    nodes,
                    edges,
                    module_fqn or "",
                    decorators=raw_decorators,
                )
                for grandchild in child.children:
                    self._walk(
                        grandchild,
                        code_bytes,
                        file_path,
                        nodes,
                        edges,
                        None,
                        import_map=import_map,
                        module_fqn=module_fqn,
                        local_types_acc=local_types_acc,
                    )

    def _get_decorator_name(self, decorator_node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the decorator's callable name from a single decorator node, or None.

        Skips the leading '@' token and any comments; passes the first real expression
        node to _get_identifier, which handles identifier / attribute / call / PEP-614
        parenthesized expressions.
        """
        for inner in decorator_node.children:
            if inner.type not in ("@", "comment"):
                name = self._get_identifier(inner, code_bytes)
                return name if name != "unknown" else None
        return None

    def _extract_decorator_names(self, node: BaseNode, code_bytes: bytes) -> list[str]:
        """Extract decorator names from a decorated_definition node."""
        names: list[str] = []
        for child in node.children:
            if child.type == "decorator":
                name = self._get_decorator_name(child, code_bytes)
                if name:
                    names.append(name)
        return names

    def _process_function_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
        decorators: list[str] | None = None,
    ) -> Node:
        """Process function or method definition node."""
        child = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(child, code_bytes)
        node_type = NodeType.METHOD if self._is_method(node) else NodeType.FUNCTION

        metadata: dict[str, Any] = {}
        if decorators:
            metadata["decorators"] = decorators
        if decorators and any(
            d == "abstractmethod" or d.endswith(".abstractmethod") for d in decorators
        ):
            metadata["is_abstract"] = True

        func_node = Node(
            id=node_id,
            type=node_type,
            name=node_name,
            file_path=file_path,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            language=self.LANG,
            metadata=metadata,
        )
        nodes.append(func_node)

        edges.extend(
            Edge(
                id=f"{node_id}:decorator:{i}:{deco_name}",
                type=EdgeType.CALLS,
                source=node_id,
                target=f"raw_call:{deco_name}",
                confidence=0.5,
                file_path=file_path,
            )
            for i, deco_name in enumerate(decorators or [])
        )

        parts = node_id.rsplit(".", maxsplit=1)
        parent_fqn = parts[0] if len(parts) > 1 else module_fqn
        edge_type = EdgeType.DECLARES if node_type == NodeType.METHOD else EdgeType.CONTAINS
        edges.append(
            Edge(
                id=f"{parent_fqn}:structural:{node_id}",
                type=edge_type,
                source=parent_fqn,
                target=node_id,
                confidence=1.0,
                file_path=file_path,
            )
        )
        return func_node

    def _process_class_node(
        self,
        node: BaseNode,
        code_bytes: bytes,
        file_path: str,
        nodes: list[Node],
        edges: list[Edge],
        module_fqn: str,
        decorators: list[str] | None = None,
    ) -> None:
        """Process class definition node."""
        child = node.child_by_field_name("name")
        node_id = self._get_id(node, code_bytes, file_path)
        node_name = self._extract_node_name(child, code_bytes)

        superclass_names = self._collect_superclasses(node, node_id, file_path, code_bytes, edges)

        metadata: dict[str, Any] = {}
        if decorators:
            metadata["decorators"] = decorators
        if self._is_abstract_class(superclass_names, decorators):
            metadata["is_abstract"] = True

        nodes.append(
            Node(
                id=node_id,
                type=NodeType.CLASS,
                name=node_name,
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                language=self.LANG,
                metadata=metadata,
            )
        )

        parts = node_id.rsplit(".", maxsplit=1)
        parent_fqn = parts[0] if len(parts) > 1 else module_fqn
        edges.append(
            Edge(
                id=f"{parent_fqn}:structural:{node_id}",
                type=EdgeType.CONTAINS,
                source=parent_fqn,
                target=node_id,
                confidence=1.0,
                file_path=file_path,
            )
        )

    def _collect_superclasses(
        self,
        node: BaseNode,
        node_id: str,
        file_path: str,
        code_bytes: bytes,
        edges: list[Edge],
    ) -> list[str]:
        """Collect superclass names and emit EXTENDS edges for a class definition node."""
        names: list[str] = []
        superclasses_node = node.child_by_field_name("superclasses")
        if not superclasses_node:
            return names
        for sc in superclasses_node.children:
            if sc.type in ("identifier", "attribute", "subscript"):
                sc_name = self._get_identifier(sc, code_bytes)
                if sc_name != "unknown":
                    names.append(sc_name)
                    edges.append(
                        Edge(
                            id=f"{node_id}:extends:{sc_name}",
                            type=EdgeType.EXTENDS,
                            source=node_id,
                            target=f"raw_class:{sc_name}",
                            confidence=1.0,
                            file_path=file_path,
                        )
                    )
            elif sc.type == "keyword_argument":
                meta_name = self._extract_metaclass_name(sc, code_bytes)
                if meta_name:
                    names.append(meta_name)
        return names

    @staticmethod
    def _is_abstract_class(superclass_names: list[str], decorators: list[str] | None) -> bool:
        """Return True if class should be marked abstract (ABC/ABCMeta in bases or decorators)."""
        candidates = (*superclass_names, *(decorators or []))
        return any(n in _ABC_NAMES or n.rpartition(".")[-1] in _ABC_NAMES for n in candidates)

    def _extract_metaclass_name(self, node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the metaclass name from a keyword_argument node like `metaclass=ABCMeta`."""
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node and value_node and self._get_identifier(name_node, code_bytes) == "metaclass":
            meta_name = self._get_identifier(value_node, code_bytes)
            return meta_name if meta_name != "unknown" else None
        return None

    def _process_call_node(
        self, node: BaseNode, code_bytes: bytes, file_path: str, source_id: str, edges: list[Edge]
    ) -> None:
        """
        Finds call expressions node.
        """
        child = node.child_by_field_name("function")
        edge_id = f"{file_path}:edge_{node.start_byte}_{node.end_byte}"
        if child:
            call_name = self._get_identifier(child, code_bytes)
            if call_name == "unknown":
                return
            target_id = f"raw_call:{call_name}"

            edges.append(
                Edge(
                    id=edge_id,
                    type=EdgeType.CALLS,
                    source=source_id,
                    target=target_id,
                    confidence=0.5,
                    context=f"Call to {call_name}",
                    file_path=file_path,
                    line_number=node.start_point.row + 1,
                )
            )
            if call_name in self._DI_CALL_NAMES:
                provider = self._di_provider_name(node, code_bytes)
                if provider:
                    edges.append(
                        Edge(
                            id=f"{file_path}:dep_{node.start_byte}_{node.end_byte}",
                            type=EdgeType.DEPENDS_ON,
                            source=source_id,
                            target=f"raw_call:{provider}",
                            confidence=0.5,
                            context=f"DI dependency on {provider}",
                            file_path=file_path,
                            line_number=node.start_point.row + 1,
                        )
                    )

    def _di_provider_name(self, call_node: BaseNode, code_bytes: bytes) -> str | None:
        """Return the first positional argument's identifier/dotted name, or None.

        None for argless calls, keyword-only calls, and non-name arguments
        (lambdas, calls, subscripts) — those emit no DEPENDS_ON edge (spec §3.2a/b).
        """
        args = call_node.child_by_field_name("arguments")
        if not args:
            return None
        for child in args.named_children:
            if child.type == "keyword_argument":
                continue
            if child.type in ("identifier", "attribute"):
                name = self._get_identifier(child, code_bytes)
                return name if name != "unknown" else None
            return None
        return None

    def _resolve_type_fqn(
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
        return (
            f"{file_path_to_module_fqn(file_path, self._pick_source_root(file_path))}.{type_name}"
        )

    def _collect_assignment_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        func_node: Node,
        acc: dict[str, dict[str, str]],
    ) -> None:
        """Populate acc with var→FQN for `var = ClassName(...)` assignments."""
        left_node = node.child_by_field_name("left")
        right_node = node.child_by_field_name("right")
        if not left_node or not right_node or right_node.type != "call":
            return
        var_name = self._get_identifier(left_node, code_bytes)
        if var_name == "unknown":
            return
        func_call_node = right_node.child_by_field_name("function")
        if not func_call_node:
            return
        class_name = self._get_identifier(func_call_node, code_bytes)
        if class_name == "unknown":
            return
        if "." in class_name:
            # Module-qualified constructor (e.g. models.Store()): keep only if prefix
            # is a known import alias. Otherwise it's a method-call result — skip.
            module_part, _, _ = class_name.partition(".")
            if not import_map or module_part not in import_map:
                return
        acc.setdefault(func_node.id, {})[var_name] = self._resolve_type_fqn(
            class_name, import_map, func_node.file_path
        )

    def _collect_param_type(
        self,
        node: BaseNode,
        code_bytes: bytes,
        import_map: dict[str, str] | None,
        func_node: Node,
        acc: dict[str, dict[str, str]],
    ) -> None:
        """Populate acc with param→FQN for typed parameter annotations."""
        if not node.named_children:
            return
        name_node = node.named_children[0]
        type_node = node.child_by_field_name("type")
        if not type_node:
            return
        var_name = self._get_identifier(name_node, code_bytes)
        if var_name == "unknown":
            return
        # Slice raw bytes to capture union/generic types like `A | None` or `list[X]`
        raw_type = (
            code_bytes[type_node.start_byte : type_node.end_byte].decode("utf-8").strip("\"'")
        )
        clean_type = self._clean_python_type_string(raw_type)
        if not clean_type:
            return
        acc.setdefault(func_node.id, {})[var_name] = self._resolve_type_fqn(
            clean_type, import_map, func_node.file_path
        )

    _GENERIC_WRAPPERS: frozenset[str] = frozenset({"Optional", "Union"})
    _DI_CALL_NAMES: frozenset[str] = frozenset({"Depends", "Security"})

    def _clean_python_type_string(self, type_str: str) -> str:
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
                return self._clean_python_type_string(first_arg)
            type_str = outer
        return type_str.strip()

    def _is_method(self, node: BaseNode) -> bool:
        """Check if a function node is a method (defined inside a class)."""
        curr = node.parent
        while curr:
            if curr.type == "class_definition":
                return True
            if curr.type in ("function_definition", "async_function_definition"):
                return False
            curr = curr.parent
        return False

    def _get_identifier(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract name from AST node using byte slicing."""
        if node.type == "identifier":
            start, end = node.start_byte, node.end_byte
            return code_bytes[start:end].decode("utf8", errors="replace")
        if node.type == "parenthesized_expression":
            for child in node.children:
                if child.type not in ("(", ")", "comment"):
                    return self._get_identifier(child, code_bytes)
        if node.type in ("attribute", "call", "subscript"):
            return self._extract_nested_name(node, code_bytes)
        return "unknown"

    def _extract_node_name(self, node: BaseNode | None, code_bytes: bytes) -> str:
        """Extract node name from name node using byte slicing."""
        if node:
            start, end = node.start_byte, node.end_byte
            return code_bytes[start:end].decode("utf8", errors="replace")
        return "unknown"

    def _extract_object_attr_name(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract identifier from object/attribute nodes."""
        obj_node = node.child_by_field_name("object")
        attr_node = node.child_by_field_name("attribute")
        if obj_node and attr_node:
            obj_id = self._get_identifier(obj_node, code_bytes)
            attr_id = self._get_identifier(attr_node, code_bytes)
            defined = obj_id != "unknown" and attr_id != "unknown"
            return f"{obj_id}.{attr_id}" if defined else "unknown"
        if attr_node:
            return self._get_identifier(attr_node, code_bytes)
        return "unknown"

    def _extract_nested_name(self, node: BaseNode, code_bytes: bytes) -> str:
        """Extract nested identifier from attribute/call/subscript nodes."""
        if node.type == "attribute":
            return self._extract_object_attr_name(node, code_bytes)
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                return self._get_identifier(func_node, code_bytes)
        elif node.type == "subscript":
            value_node = node.child_by_field_name("value")
            if value_node:
                return self._get_identifier(value_node, code_bytes)
        return "unknown"
