"""JS/TS runtime globals, and the synthetic namespace calls on them resolve into (#111).

Python builtins need no such pass: `classify_fqn` recognises `len` or `json.loads`
from the interpreter's own `builtins` and `sys.stdlib_module_names`. A TypeScript
call to `Math.max` has no equivalent to consult, so it left the resolver as a bare
`Math.max` classified UNKNOWN — counted as an unresolved call and indistinguishable
from a genuine resolution miss.

Calls on a global are rewritten to `js_builtins.<name>`. The prefix is what keeps
this language-scoped: only a JS/TS source ever produces it, so `classify_fqn` can
map the root to STDLIB without a Python module that happens to call something
`Map.get` being mistaken for the JavaScript runtime.
"""

from pathlib import PurePosixPath

JS_BUILTINS_ROOT = "js_builtins"

#: Extensions whose calls are JavaScript at runtime. Matches the extractors
#: registered in `extractors/registry.py` for `.ts`/`.tsx`.
_JS_SUFFIXES: frozenset[str] = frozenset({".ts", ".tsx"})

#: ECMAScript standard globals plus the browser and Node host objects a UI or
#: server codebase calls directly. A receiver is matched by its root segment only,
#: so `Math.max` and `document.body.append` both qualify; `arr.map` does not.
JS_GLOBALS: frozenset[str] = frozenset(
    {
        # ECMAScript value/function globals
        "globalThis",
        "isFinite",
        "isNaN",
        "parseFloat",
        "parseInt",
        "encodeURI",
        "encodeURIComponent",
        "decodeURI",
        "decodeURIComponent",
        "structuredClone",
        "queueMicrotask",
        # ECMAScript constructors and namespaces
        "Array",
        "ArrayBuffer",
        "Atomics",
        "BigInt",
        "Boolean",
        "DataView",
        "Date",
        "Error",
        "EvalError",
        "Float32Array",
        "Float64Array",
        "Function",
        "Int8Array",
        "Int16Array",
        "Int32Array",
        "Intl",
        "JSON",
        "Map",
        "Math",
        "Number",
        "Object",
        "Promise",
        "Proxy",
        "RangeError",
        "ReferenceError",
        "Reflect",
        "RegExp",
        "Set",
        "String",
        "Symbol",
        "SyntaxError",
        "TypeError",
        "Uint8Array",
        "Uint8ClampedArray",
        "Uint16Array",
        "Uint32Array",
        "URIError",
        "WeakMap",
        "WeakRef",
        "WeakSet",
        # Host globals shared by browsers and Node
        "console",
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "fetch",
        "URL",
        "URLSearchParams",
        "AbortController",
        "TextEncoder",
        "TextDecoder",
        "Blob",
        "FormData",
        "Headers",
        "Request",
        "Response",
        "crypto",
        "performance",
        # Browser globals
        "window",
        "document",
        "navigator",
        "location",
        "history",
        "localStorage",
        "sessionStorage",
        "requestAnimationFrame",
        "cancelAnimationFrame",
        "getComputedStyle",
        "matchMedia",
        "alert",
        "confirm",
        "prompt",
        "ResizeObserver",
        "IntersectionObserver",
        "MutationObserver",
        "CustomEvent",
        "Event",
        "WebSocket",
        "Worker",
        "Image",
        # Node globals
        "process",
        "Buffer",
        "require",
        "setImmediate",
        "clearImmediate",
    }
)


def js_builtin_target(raw_name: str, file_path: str | None) -> str | None:
    """The `js_builtins.*` FQN for a call on a JS global, or None when it is not one.

    `file_path` is the calling source's file; without it the language is unknown
    and nothing is rewritten.
    """
    if file_path is None or PurePosixPath(file_path.replace("\\", "/")).suffix not in _JS_SUFFIXES:
        return None
    if raw_name.split(".", maxsplit=1)[0] not in JS_GLOBALS:
        return None
    return f"{JS_BUILTINS_ROOT}.{raw_name}"
