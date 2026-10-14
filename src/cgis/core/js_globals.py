"""The names a JavaScript runtime provides without an import (#111).

Plain data in `core` because two layers read it: the TypeScript extractor, to
record which of these names a file rebinds, and the resolver, to rewrite calls
on the ones it does not. `specifier_fqn` is here for the same reason: the
extractor spells import targets with it and the resolver matches against them.
"""

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
        "global",
        "process",
        "Buffer",
        "require",
        "setImmediate",
        "clearImmediate",
    }
)


def specifier_fqn(specifier: str) -> str:
    """A bare module specifier dotted the way import targets are: `@x/a/b` -> `@x.a.b`."""
    return specifier.replace("/", ".")


#: The synthetic root calls on a JS runtime global resolve into (#111).
JS_BUILTINS_ROOT = "js_builtins"

#: The `node:` scheme, which names a Node builtin and nothing else (#556).
NODE_SCHEME = "node:"

#: Node's builtin modules importable without the `node:` scheme — Node's own
#: `module.builtinModules`, minus internals and the scheme-only ones (`node:test`,
#: `node:sqlite`). Matched on the specifier's root, so `fs/promises` counts (#556).
NODE_BUILTIN_MODULES: frozenset[str] = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    }
)
