# 🔎 Architecture Audit

**A measured read of your codebase's structure, delivered in five working days. $2,400 fixed.**

CGIS is free and you can run it yourself. This is for teams who want the analysis rather than the tool: someone who has read a lot of these graphs telling you what yours says.

---

## What you get

A written report, the graph itself, and ninety minutes on a call to walk through it.

| Section | Measured with | The question it answers |
| :--- | :--- | :--- |
| **Authorisation coverage** | `cgis_audit_reachability` | Does every route handler actually reach its authz guard? |
| **Blast radius** | `cgis_analyze_impact` | What breaks if we make the three changes we are planning? |
| **Coupling and god-classes** | `cgis_metrics` | Which parts will fight back hardest during a refactor? |
| **Architectural drift** | `cgis_drift` | How far has each domain moved from the pattern we said we follow? |
| **Graph integrity** | `cgis_validate` | How much of this codebase is even amenable to static analysis? |

The first row is the one that usually pays for the engagement. *"Is there a route in our API that reaches the database without passing a permission check?"* is a question with money attached, and nobody checks it by hand because on a few hundred endpoints it is a week of tedium. On the graph it is a traversal.

The report names specific handlers, files and line numbers. It is not a maturity score out of ten.

**You keep the graph.** The `graph.db` is handed over with the report, so your team can keep asking it questions after the engagement ends — including from Claude Code, via the plugin.

---

## How it runs

1. **Access.** A read-only clone or a tarball. No production access, no credentials, no environment.
2. **Ingest.** Your repositories go into a graph. If resolution comes out unusually low, you hear about it on day one, not in the report.
3. **Analysis.** The five sections above, plus whatever the graph turns up that I did not expect. That last part is usually the interesting one.
4. **Report.** Written, specific, with file and line references throughout.
5. **Walkthrough.** Ninety minutes with whoever needs to act on it. Recorded if you want it.

Five working days from receiving the code.

---

## Price

**$2,400** for up to **three repositories** and **150,000 lines** of Python and TypeScript.

Larger estates are quoted after a look at the tree — the honest number depends on how many resolution edge cases the code has, not on line count alone. There is no hourly rate and no change-order mechanism: the scope above is the scope.

**Included:** the report, the graph, the walkthrough, and one round of follow-up questions by email within thirty days.

**Not included:** implementing the fixes. If you want the drift gates wired into CI or Guardian reviewing your pull requests, that is separate work and we can talk about it after you have read the report — not before.

---

## What this is not

The graph is static structure. That boundary is real, and it is better stated here than discovered halfway through.

- **Not a penetration test.** Authorisation *coverage* means every handler has a static path to a guard. It does not mean the guard is correct, that the logic behind it is sound, or that the endpoint is unexploitable.
- **Not a runtime analysis.** Nothing is executed. Behaviour that only appears under load, concurrency or real data is out of scope.
- **Not a business-logic review.** The report says how your code is wired, not whether the wiring implements the right rules.
- **Not language-complete.** Extractors cover Python and TypeScript. Vue, Astro, Go, Rust and everything else are invisible to the graph, and the report will say which parts of your codebase it could not see.
- **Not a substitute for judgement.** An empty impact result means *no static callers found*, not *safe to delete*. Dynamic dispatch, registries and framework decorators do not appear in any static graph. The report flags where this matters instead of asserting a safety it cannot see.

Roughly a tenth of call edges in a typical Python codebase cannot be resolved statically at all — they are calls on objects typed at runtime. That number is in the report too, because it tells you how much of the analysis to trust.

---

## Who this is for

**A good fit if:** you have a Python or TypeScript codebase large enough that no one holds it in their head, you are planning a migration or a significant refactor, you are onboarding AI agents onto the codebase and getting confident nonsense back, or someone has asked whether your authorisation is actually complete and nobody can answer.

**A poor fit if:** the codebase is small enough to read, the languages are outside the extractors, or what you actually need is a security assessment — in which case hire a security firm, and this is at best a first pass that tells them where to look.

---

## Evidence

Before commissioning anything, read the [case study](CASE_STUDY.md). It is CGIS run against a real twelve-repository estate — 8,146 commits, four languages — with every figure measured and a section on what the analysis could not cover.

If that page reads as honest to you, this engagement will run the same way. If it does not, we should not work together.

---

## Get in touch

- **Email:** zaebuntu@gmail.com
- **GitHub:** [open an issue](https://github.com/zaebee/codegraph-brain/issues) — fine for questions about scope, though most people prefer email for anything involving their own codebase

Tell me roughly how large the codebase is and what you are trying to decide. If the audit is not the right thing for your situation, I will say so.
