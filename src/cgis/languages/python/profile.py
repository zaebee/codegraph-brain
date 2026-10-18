"""Python's answers to the core clauses that need per-language facts.

Data only, no logic: the clauses are docs/specs/core/ontology.yml. A Python module
rather than a data file, so the import walk behind both fingerprints (extraction,
#617; review) sees it: a data file is invisible to that walk.
"""

from collections.abc import Mapping
from types import MappingProxyType

#: CV-1: a library callable that may invoke one of its arguments, keyed by what the
#: Python resolver produces for the call (a builtin, which resolves to nothing, by its
#: own name), with the slots it invokes: a 0-based position or a keyword name (#632).
INVOKERS: Mapping[str, frozenset[int | str]] = MappingProxyType(
    {
        "map": frozenset({0}),
        "filter": frozenset({0}),
        "sorted": frozenset({"key"}),
        "min": frozenset({"key"}),
        "max": frozenset({"key"}),
        "functools.reduce": frozenset({0}),
        "threading.Thread": frozenset({1, "target"}),
        "threading.Timer": frozenset({1, "function"}),
        "multiprocessing.Process": frozenset({1, "target"}),
        "asyncio.to_thread": frozenset({0}),
        "concurrent.futures.ThreadPoolExecutor.submit": frozenset({0}),
        "concurrent.futures.ThreadPoolExecutor.map": frozenset({0}),
        "concurrent.futures.ProcessPoolExecutor.submit": frozenset({0}),
        "concurrent.futures.ProcessPoolExecutor.map": frozenset({0}),
        "asyncio.get_event_loop.run_in_executor": frozenset({1}),
        "asyncio.get_running_loop.run_in_executor": frozenset({1}),
        "asyncio.get_event_loop.call_soon": frozenset({0}),
        "asyncio.get_running_loop.call_soon": frozenset({0}),
        "asyncio.get_event_loop.call_soon_threadsafe": frozenset({0}),
        "asyncio.get_running_loop.call_soon_threadsafe": frozenset({0}),
        "asyncio.get_event_loop.call_later": frozenset({1}),
        "asyncio.get_running_loop.call_later": frozenset({1}),
    }
)
