# Martian Code Review Bench — vendored golden comments (#342 Phase 2)

The offline corpus of
[`withmartian/code-review-benchmark`](https://github.com/withmartian/code-review-benchmark)
(MIT), copied verbatim from `offline/golden_comments/` on 2026-08-12.

Vendored rather than fetched at run time for the same reason
`benchmarks/guardian/calibration.jsonl` is committed: a published number has to
name the evidence it came from, and an upstream file that can be edited or
moved is not that. Refreshing it is a deliberate act with a visible diff.

| file | PRs | golden comments | sha256 (first 12) |
|---|---|---|---|
| `sentry.json` | 10 | 36 | `02342962b0d1` |
| `cal_dot_com.json` | 10 | 41 | `f8d88e88392b` |
| `grafana.json` | 10 | 25 | `beeb065e0127` |
| `discourse.json` | 10 | 41 | `d5d8d02f3282` |
| `keycloak.json` | 10 | 30 | `aa89db36f36f` |
| **total** | **50** | **173** | |

173 is the `all` profile. The `core` profile (158) and `strict` (139) are
severity filters over the same entries — `severity` is `High` / `Medium` /
`Low` on each comment. The dashboard's default, and the basis for every number
in the spec's recomputed leaderboard, is `core`.

## Schema

```json
{
  "pr_title": "…",
  "url": "https://github.com/<owner>/<repo>/pull/<n>",
  "az_comment": null,
  "comments": [
    {"comment": "…", "severity": "High", "category": "bug"}
  ]
}
```

**No file, no line.** Matching is purely semantic, which is why
`cgis.guardian.calibrate` renders both sides anchor-free.

## Two facts about this corpus that the spec originally got wrong

**The URLs are not all upstream.** 35 of 50 are; 15 live in the benchmark's own
fork org `ai-code-review-evaluation` — discourse entirely (10/10), plus 4 sentry
and 1 keycloak. All are public and `gh pr diff` works on all of them.

**Five PRs are flagged unreproducible by the corpus itself**, via `az_comment`:

- 4 × `"reviewed commit is not in the repo"`
- 1 × `"there is no such PR, it is a mix of many PRs"`

These are excluded and reported as excluded. Scoring them as misses would
charge Guardian for a commit nobody can fetch.

## Slices

Graph context depends on the files a PR touches, not on the repository's
headline language. Measured from `gh pr diff --name-only` on all 50:

| project | PRs | graph-enabled | diff-only |
|---|---|---|---|
| sentry | 10 | 10 | 0 |
| cal.com | 10 | 10 | 0 |
| grafana | 10 | 3 | 7 |
| discourse | 10 | 0 | 10 |
| keycloak | 10 | 0 | 10 |
| **total** | **50** | **23** | **27** |

Grafana is a Go backend with a TypeScript front end; three of its PRs are
front-end and two are purely so. This 23/27 split is what gate **G5** is
registered on — see the spec, §"Pre-registered gates".
