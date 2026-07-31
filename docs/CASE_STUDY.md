# 📊 Case Study: CGIS on a 12-Repository Estate

CGIS was not built as a demo. It was built to survive a working multi-repository codebase — twelve repositories, four languages, 8,146 commits, still shipping daily.

**Every number on this page is measured, including the inconvenient ones.** All of it is reproducible with `cgis ingest`, `cgis validate` and `git rev-list --count`.

| | |
| :--- | :--- |
| Graph built | 2026-06-14 |
| Estate measured | 2026-07-31 |
| CGIS version | 0.5.0 |

---

## The estate

A product codebase split across twelve repositories: a FastAPI backend, three frontends on three different frameworks, four content sites, an infrastructure repo, a pricing agent, and CGIS itself. Nothing was arranged for the write-up — this is what the tree looked like on the morning it was measured.

| Repository | Role | Commits | Python | TS | Vue | Astro | Last commit |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `owner-api` | REST API, workers, MCP | 4 253 | 630 | — | — | — | 7 days |
| `owner-web` | Owner dashboard | 1 342 | — | 575 | — | — | 9 days |
| `rider-web` | Booking app | 500 | — | 345 | 177 | — | 9 days |
| `memory-facets` | Game backend + webapp | 470 | 1 745 | 77 | — | 212 | 2 hours |
| `ansible` | Infrastructure | 462 | 11 | — | — | — | 3 days |
| `estate-blog` | Content site | 276 | 2 | 248 | — | 44 | 4 days |
| `blog-seo` | SEO site, 4 locales | 275 | — | 156 | — | 125 | 6 weeks |
| `ownima-admin` | Internal admin | 203 | — | 84 | — | — | 3 weeks |
| `codegraph-brain` | CGIS itself | 195 | 139 | 46 | — | — | today |
| `ownima-blog` | CMS blog | 85 | — | 29 | — | — | 8 weeks |
| `rider-app` | Mobile client | 63 | — | 141 | 79 | — | 10 days |
| `pricing-oracle` | Pricing agent | 22 | 17 | — | — | — | 5 months |
| **12 repositories** | | **8 146** | **2 544** | **1 701** | **256** | **381** | |

Source-file counts, excluding `node_modules`, `dist` and virtualenvs.

---

## The graph on the largest repository

The backend is the hard case: 512 files, a service layer, a CRUD layer, SAQ workers, an MCP server and four storage backends. It is also where an AI agent does the most damage when it guesses wrong about what calls what.

| Metric | Value |
| :--- | ---: |
| Files | 512 |
| Nodes | 10 363 |
| Edges | 40 493 |
| Edges definitively classified | **88.4 %** |

### Where every edge ends up

```
Internal    ████████████████████████  47.1%   19 055
Stdlib      ████████████              23.4%    9 471
External    █████████                 17.9%    7 268
Unresolved  ██████                    11.6%    4 699
```

| Resolution class | Edges | Share | Meaning |
| :--- | ---: | ---: | :--- |
| Internal | 19 055 | 47.1 % | Both ends are code in this repository |
| Stdlib | 9 471 | 23.4 % | Target identified as Python standard library |
| External | 7 268 | 17.9 % | Target identified as a third-party package |
| Unresolved | 4 699 | 11.6 % | No deterministic target — see below |

---

## Reading the number honestly

Forty-seven percent internal resolution sounds low until you ask what the other half is. It is not failure — it is **correct classification of code that lives outside the repository**. Standard library and third-party calls are supposed to terminate at the boundary; resolving them into the graph would be wrong, not better.

What matters is the residue. **Only 11.6 % of edges have no deterministic target at all**, against a configured gate of 30 %. And the residue is not random — it concentrates almost entirely in one pattern:

| Top unresolved target | Count | Why it cannot be resolved |
| :--- | ---: | :--- |
| `response.json` | 249 | Attribute on a runtime object with no static type |
| `logger.info` | 216 | Module-level singleton, dynamically configured |
| `logger.exception` | 194 | Same |
| `logger.warning` | 123 | Same |
| `resp.json` | 121 | Same shape as `response.json`, different local name |
| `api_client.get` | 98 | Injected client, bound at runtime |
| `router.get` / `router.post` | 147 | Framework decorator on a constructed object |

These are calls on objects whose type is decided while the program runs. No static analyser resolves them without executing the code or a full type-inference pass — and pretending otherwise is exactly the guessing CGIS exists to stop. **Logging and HTTP-response handling alone account for over 900 of the 4,699.**

> ### The claim this supports
>
> On a real 512-file backend, CGIS gives an agent a definitive answer about **88.4 % of all edges** — internal, stdlib or external — and is explicit about the remaining 11.6 % rather than inventing a target for them.
>
> An agent that knows what it does not know is a different tool from one that hallucinates a call graph. The unresolved list above is not a defect report; it is the honest boundary of static analysis, printed rather than hidden.

---

## What the graph is made of

The graph is not just a call graph. Seven edge types carry different structural facts, which is what makes transitive impact analysis possible — *if I change this function, what breaks five layers up?*

| Node type | Count | | Edge type | Count |
| :--- | ---: | :--- | :--- | ---: |
| `FUNCTION` | 4 363 | | `CALLS` | 26 956 |
| `METHOD` | 4 232 | | `DECLARES` | 4 232 |
| `CLASS` | 1 209 | | `IMPORTS` | 3 133 |
| `FILE` | 511 | | `IMPORTS_SYMBOL` | 2 618 |
| `VARIABLE` | 48 | | `CONTAINS` | 2 490 |
| | | | `EXTENDS` | 618 |
| | | | `DEPENDS_ON` | 446 |
| **Total** | **10 363** | | | **40 493** |

CGIS tracks 512 files but emits 511 `FILE` nodes — one tracked file produced no extractable node. Both figures are printed as the database holds them rather than reconciled.

The whole graph is a single SQLite file. Traversals run in milliseconds, which is what lets it sit behind an MCP server and answer an agent mid-conversation instead of behind a batch job.

---

## Limits

**What this case study does not show.**

- **Two of the four languages are not covered.** CGIS ships extractors for Python and TypeScript. That is 4,245 of the estate's 4,882 source files — **87 %** — but the 256 Vue and 381 Astro files are invisible to it. On this estate that means the content sites and part of the rider frontend are outside the graph.
- **The backend graph is six weeks old.** It was built on 2026-06-14 and the repository has moved since. Incremental re-ingest exists (`--incremental`); this snapshot simply was not refreshed for the write-up.
- **Cross-repository edges are not measured here.** Each graph in this study is single-repo. The frontends consume the backend through generated OpenAPI types, and that link is not represented.
- **No before/after on agent accuracy.** This page measures the graph, not the downstream effect on an LLM's output. That benchmark is separate work and is not claimed here.

---

## Reproducing this

```bash
pip install codegraph-brain

cgis ingest <path> --output graph.db
cgis validate --db graph.db
```

Estate figures come from `git rev-list --count HEAD` per repository and a `find` over source extensions, excluding `node_modules`, `dist` and virtualenvs.
