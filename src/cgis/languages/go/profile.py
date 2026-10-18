"""Go's answers to the core clauses that need per-language facts.

Data only, no logic: the clauses are docs/specs/core/ontology.yml. A Python module
rather than a data file, so the import walk behind both fingerprints (extraction,
#617; review) sees it: a data file is invisible to that walk.
"""

from types import MappingProxyType

#: CV-1: a library function or method that may invoke one of its arguments, keyed by
#: its resolved target (an import path spelled with dots), with the 0-based argument
#: positions it invokes.
INVOKERS = MappingProxyType(
    {
        "sync.Once.Do": frozenset({0}),
        "sync.WaitGroup.Go": frozenset({0}),
        "time.AfterFunc": frozenset({1}),
        "golang.org.x.sync.errgroup.Group.Go": frozenset({0}),
        "golang.org.x.sync.errgroup.Group.TryGo": frozenset({0}),
        "context.AfterFunc": frozenset({1}),
        "sort.Slice": frozenset({1}),
        "sort.SliceStable": frozenset({1}),
        "sort.Search": frozenset({1}),
        "slices.SortFunc": frozenset({1}),
        "slices.SortStableFunc": frozenset({1}),
        "slices.IndexFunc": frozenset({1}),
        "slices.ContainsFunc": frozenset({1}),
        "slices.DeleteFunc": frozenset({1}),
        "strings.Map": frozenset({0}),
        "strings.FieldsFunc": frozenset({1}),
        "strings.IndexFunc": frozenset({1}),
        "strings.TrimFunc": frozenset({1}),
        "strings.LastIndexFunc": frozenset({1}),
        "strings.TrimLeftFunc": frozenset({1}),
        "strings.TrimRightFunc": frozenset({1}),
        "strings.ContainsFunc": frozenset({1}),
        "bytes.Map": frozenset({0}),
        "bytes.FieldsFunc": frozenset({1}),
        "bytes.IndexFunc": frozenset({1}),
        "bytes.LastIndexFunc": frozenset({1}),
        "bytes.TrimFunc": frozenset({1}),
        "bytes.ContainsFunc": frozenset({1}),
        "path.filepath.Walk": frozenset({1}),
        "path.filepath.WalkDir": frozenset({1}),
    }
)
#: The last segment of every invoker: a call named otherwise passes no value worth a raw edge.
INVOKER_NAMES = frozenset(target.rsplit(".", 1)[-1] for target in INVOKERS)
