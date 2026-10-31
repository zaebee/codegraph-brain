# release-please CI — Design

**Date:** 2026-06-12
**Status:** Approved (brainstorming)
**Scope:** Out-of-scope sprint task — automated versioning + changelog + GitHub Release. No package publishing.

## Goal

Introduce [release-please](https://github.com/googleapis/release-please) so that merging
Conventional-Commit PRs into `main` automatically maintains a versioned changelog and cuts
GitHub Releases, without any manual version bookkeeping.

The repo already satisfies the prerequisite: commits follow Conventional Commits
(`feat`/`fix`/`docs`/`perf`/`refactor` with scopes) and PRs are squash-merged (one
conventional commit per PR).

## Decisions

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Release scope | **Versioning only** (CHANGELOG + tag + GitHub Release) | Minimal moving parts, no secrets; publishing deferred |
| Config style | **Manifest mode** (`release-please-config.json` + `.release-please-manifest.json`) | Modern, future-proofs PyPI/monorepo additions |
| Release type | `python` | Understands `pyproject.toml` version field |
| Pre-1.0 bump | **Default (`bump-minor-pre-major` unset)** → `feat` = patch, breaking = minor | User wants to grow versions slowly; major stays at 0 |
| 1.0 transition | Manual only (`Release-As: 1.0.0`) | release-please never auto-jumps to 1.0 in pre-major mode |
| Auth | Default `GITHUB_TOKEN` | No PAT/secret to manage for versioning-only flow |

## Architecture

```
.github/workflows/release-please.yml   ← new workflow, trigger: push → main
release-please-config.json             ← release-type=python, pre-major bump settings
.release-please-manifest.json          ← state: { ".": "0.1.0" }  (seeded with current version)
```

### Flow

1. A `feat`/`fix`/... PR is squash-merged into `main` (current practice).
2. `release-please.yml` runs on `push` to `main`.
3. release-please parses conventional commits since the last release and opens/updates a
   **Release PR** containing: bumped `version` in `pyproject.toml`, generated/updated
   `CHANGELOG.md`, updated `.release-please-manifest.json`.
4. Merging the Release PR makes release-please create the git tag `v0.1.x` and publish a
   **GitHub Release** with auto-generated notes. Nothing is published to PyPI.

## Configuration detail

`release-please-config.json` (single root package):

- `"release-type": "python"`
- `"packages": { ".": {} }`
- `bump-minor-pre-major` / `bump-patch-for-minor-pre-major`: **omitted** (default behavior =
  variant A).
- Default changelog sections; no custom `changelog-sections` needed initially.

`.release-please-manifest.json`:

- `{ ".": "0.1.0" }` — seeds the current version so the first Release PR bumps from `0.1.0`
  rather than bootstrapping from scratch.

`.github/workflows/release-please.yml`:

- Trigger: `on: push: branches: [main]`
- Permissions: `contents: write`, `pull-requests: write`
- Single job using `googleapis/release-please-action@v4` with
  `token: ${{ secrets.GITHUB_TOKEN }}`, pinned by commit SHA to match the repo's existing
  action-pinning convention (see `ci.yml`, `autodoc.yml`).

## Known limitation (accepted)

GitHub does not trigger workflows from events created by the default `GITHUB_TOKEN`
(anti-recursion). Consequences:

- The **Release PR will not run `ci.yml`** (no green check). Acceptable because the
  constituent commits already passed CI before squash-merge, and `main` is **not** a
  protected branch — nothing blocks the merge.
- A future PyPI workflow listening on `release: published` would **not** fire from a
  `GITHUB_TOKEN`-created release; it will need a PAT or GitHub App token. Deferred to backlog.

## Out of scope → backlog (gh issues)

1. **PyPI trusted publishing** — workflow on `release: published` that builds via hatchling
   and uploads to PyPI (OIDC), plus PAT/App-token so the release event triggers it.
2. **GitHub release artifacts** — attach built wheel/sdist to each GitHub Release (no PyPI).

## Testing / verification

Config-only change; no unit tests. Verification:

- `release-please-config.json` and `.release-please-manifest.json` are valid JSON.
- Workflow YAML parses (e.g. `actionlint` or GitHub's own validation on push).
- After merge to `main`, confirm release-please opens a Release PR bumping `0.1.0 → 0.1.1`
  (under variant A both `fix` and `feat` are patch bumps, so the first release is a patch).
