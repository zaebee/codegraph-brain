"""TypeScript module FQNs from file paths — no tree-sitter, so a pack names files grammar-free."""

#: `Node.language` for everything this extractor emits, `.ts` and `.tsx` alike;
#: the field defaults to "python", which every TS node used to be stored as (#504).
TYPESCRIPT_LANG = "typescript"


def file_path_to_module_fqn(file_path: str, source_root: str | None = None) -> str:
    """Convert a TS/TSX file path to a dot-separated module namespace.

    Examples:
        src/api/handler.ts       -> src.api.handler
        src/components/index.tsx -> src.components
        C:\\path\\to\\mod.ts     -> path.to.mod

        With source_root="src":
        src/api/handler.ts       -> api.handler
    """
    clean = file_path
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.replace("\\", "/").lstrip("/")
    if source_root:
        # removeprefix("./") so CI-style roots ("cgis ingest ./src") strip too,
        # matching the Python helper. Without it a collector configured with
        # source_root="./src" would leave the prefix on and derive an FQN no
        # node carries (#344).
        sr = source_root.replace("\\", "/").removeprefix("./").strip("/") + "/"
        if clean.startswith(sr):
            clean = clean[len(sr) :]
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if clean.endswith(ext):
            clean = clean[: -len(ext)]
            break
    if clean.endswith("/index"):
        clean = clean[:-6]
    return clean.replace("/", ".")
