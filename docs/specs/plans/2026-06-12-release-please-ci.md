# release-please CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add release-please (versioning-only) so merging Conventional-Commit PRs into `main` auto-maintains `CHANGELOG.md`, bumps `pyproject.toml`, and cuts GitHub Releases.

**Architecture:** Manifest-mode release-please. A `push`-to-`main` workflow runs `googleapis/release-please-action@v4` with the default `GITHUB_TOKEN`; it opens/updates a Release PR and, on merge, tags + publishes a GitHub Release. No package publishing.

**Tech Stack:** GitHub Actions, release-please v4 (`release-type: python`), JSON config + manifest.

**Spec:** `docs/specs/2026-06-12-release-please-ci-design.md`

---

### Task 1: release-please config + manifest

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`

- [ ] **Step 1: Create `release-please-config.json`**

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    ".": {
      "release-type": "python",
      "package-name": "codegraph-brain"
    }
  }
}
```

Note: `bump-minor-pre-major` and `bump-patch-for-minor-pre-major` are intentionally omitted
(variant A — default pre-major behavior: `feat`=patch, breaking=minor, major stays at 0).

- [ ] **Step 2: Create `.release-please-manifest.json`**

```json
{
  ".": "0.1.0"
}
```

This seeds the current version so the first Release PR bumps from `0.1.0`.

- [ ] **Step 3: Validate both files are valid JSON**

Run: `python3 -c "import json; json.load(open('release-please-config.json')); json.load(open('.release-please-manifest.json')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "ci: add release-please config + manifest (versioning-only)"
```

---

### Task 2: release-please workflow

**Files:**
- Create: `.github/workflows/release-please.yml`

- [ ] **Step 1: Create `.github/workflows/release-please.yml`**

```yaml
name: Release Please

on:
  push:
    branches: [ main ]

permissions:
  contents: write
  pull-requests: write

jobs:
  release-please:
    name: Release Please
    runs-on: ubuntu-latest
    steps:
      - name: Run release-please
        uses: googleapis/release-please-action@5c625bfb5d1ff62eadeeb3772007f7f66fdcf071  # v4.4.1
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

- [ ] **Step 2: Validate workflow YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release-please.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: (optional) Lint with actionlint if available**

Run: `command -v actionlint >/dev/null && actionlint .github/workflows/release-please.yml || echo "actionlint not installed — skipping"`
Expected: no errors, or the skip message.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release-please.yml
git commit -m "ci: add release-please workflow on push to main"
```

---

### Task 3: Open PR

**Files:** none (git/gh operations)

- [ ] **Step 1: Push branch**

```bash
git push -u origin worktree-release-please-ci
```

- [ ] **Step 2: Open PR against `main`**

```bash
gh pr create --base main --title "ci: add release-please (versioning-only)" \
  --body "Adds release-please in manifest mode for automated CHANGELOG + version bump + GitHub Release. Versioning only — no PyPI. See docs/specs/2026-06-12-release-please-ci-design.md.

Pre-1.0 bump = default (feat=patch, breaking=minor, major stays 0). Uses default GITHUB_TOKEN.

Known limitation: the future Release PR will not run ci.yml (GITHUB_TOKEN anti-recursion); main is unprotected so this does not block merges.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Expected: PR URL printed.

---

### Task 4: File backlog issues

**Files:** none (gh operations)

- [ ] **Step 1: Create PyPI publishing issue**

```bash
gh issue create --title "ci: PyPI trusted publishing on release: published" \
  --body "Follow-up to release-please (versioning-only). Add a workflow triggered on \`release: published\` that builds the package via hatchling and uploads to PyPI using OIDC trusted publishing. Note: a GITHUB_TOKEN-created release does NOT trigger workflows, so this needs a PAT or GitHub App token to fire. Depends on the release-please flow being live."
```

- [ ] **Step 2: Create GitHub release-artifacts issue**

```bash
gh issue create --title "ci: attach wheel/sdist artifacts to GitHub Release" \
  --body "Follow-up to release-please (versioning-only). Build wheel + sdist via hatchling and attach them to each GitHub Release (no PyPI upload). Lighter-weight alternative/complement to PyPI publishing."
```

Expected: two issue URLs printed.

---

## Verification (post-merge, manual)

After this PR is merged to `main`, release-please runs and should open a **Release PR**
titled like `chore(main): release 0.1.1`, containing a bumped `pyproject.toml` version, a new
`CHANGELOG.md`, and an updated `.release-please-manifest.json`. Merging that Release PR creates
tag `v0.1.1` and a GitHub Release. No PyPI upload occurs.
