# Labelling the held-out breakages

Written before the mining run; it landed in the same commit as the mined set, see
`SEALING.md` (spec:
`docs/specs/2026-10-18-data-key-layer-design.md`, Evaluation, Sets). The labeller is an
independent agent that sees each (breaking PR, fix PR) pair, and never the output of the
check.

## The rule, verbatim from the spec

**In zone** means the breakage travelled through a data key or an inheritance link *as a
person reading the code would see it*, whether or not this design can see it (a key whose
namespace only data flow would reveal is still in zone). The label does not depend on the
design, so a narrow design can fail by construction; that is the point.

## What to write for each pair

1. `zone`: `in` or `out`, by the rule above and nothing else. Do not ask whether the check
   could find it.
2. `broken_site`: the place that later broke, as `{file, id}`. `file` is the path inside
   the repository. `id` is the node id the check should list (a dotted name relative to the
   ingest root `ownima-backend/app`, for example `models.owner.UserAdmin`) or, for a key,
   the key path (for example `picture`). A breakage the fix repaired in several places
   names the first place a reader of the breaking PR's diff would have to look.
3. `reason`: one sentence on how the breakage travelled (key, inheritance link, or what
   else for an out-of-zone case).

An out-of-zone pair has `broken_site: null`. Do not stretch "in zone" to a pair the rule
does not cover, and do not shrink it to a pair this design cannot see.
