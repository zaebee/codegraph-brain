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

from cgis.core.js_globals import JS_BUILTINS_ROOT, JS_GLOBALS


def js_builtin_target(raw_name: str, shadowed: frozenset[str] = frozenset()) -> str | None:
    """The `js_builtins.*` FQN for a call on a JS global, or None when it is not one.

    `shadowed` holds the globals the calling file imports or declares itself —
    `import history from './history'`, `const confirm = ...`, a parameter named
    `process` — which are the file's own values, not the runtime's.
    Only the TypeScript pack calls this, so the source is already JavaScript.
    """
    root = raw_name.split(".", maxsplit=1)[0]
    if root not in JS_GLOBALS or root in shadowed:
        return None
    return f"{JS_BUILTINS_ROOT}.{raw_name}"
