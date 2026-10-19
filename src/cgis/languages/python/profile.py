"""Python's answers to the core clauses that need per-language facts.

Data only, no logic: the clauses are docs/specs/core/ontology.yml. A Python module
rather than a data file, so the import walk behind both fingerprints (extraction,
#617; review) sees it: a data file is invisible to that walk.
"""

from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class KeyProfile:
    """DK-1..DK-3: the library calls and shapes whose strings address data (data only)."""

    #: A dict literal keyed by one of these reads its child's keys as field paths, or
    #: the string under a child `"field"` key (an aggregation), as OpenSearch reads them.
    query_operators: frozenset[str]
    #: Storage methods, with the keyword argument that names their namespace (`index=`).
    storage_calls: Mapping[str, str]
    #: The storage methods that write the dict literals handed to them.
    storage_writes: frozenset[str]
    #: Arguments of a storage call holding a document; nested inside a written literal,
    #: such a key (`update`'s `{"doc": {...}}`) adds nothing to the path.
    write_arguments: frozenset[str]
    #: A class whose direct base ends in one of these declares its annotated fields.
    model_bases: frozenset[str]
    #: Redis methods, read or written, on a receiver whose name mentions redis.
    redis_reads: frozenset[str]
    redis_writes: frozenset[str]
    #: Calls naming a task by its first argument: they write a task-name key.
    task_methods: frozenset[str]


KEY_PROFILE = KeyProfile(
    query_operators=frozenset(
        {"term", "terms", "match", "match_phrase", "range", "prefix", "wildcard", "exists", "sort"}
    ),
    storage_calls=MappingProxyType(
        {
            "search": "index",
            "index": "index",
            "update": "index",
            "delete_by_query": "index",
            "count": "index",
        }
    ),
    storage_writes=frozenset({"index", "update"}),
    write_arguments=frozenset({"body", "document", "doc"}),
    model_bases=frozenset({"BaseModel", "SQLModel"}),
    redis_reads=frozenset({"get", "hget", "exists"}),
    redis_writes=frozenset({"set", "hset", "delete"}),
    task_methods=frozenset({"enqueue"}),
)
