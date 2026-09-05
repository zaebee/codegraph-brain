.PHONY: format lint type-check pytest doc-coverage coverage coverage-html check \
        ui-dev ui-build ui-test ui-lint ui-format ui-preview ui-analyze ui-check \
        all-checks

# ── Python ────────────────────────────────────────────────────────────────────

format:
	uv run ruff format .

lint:
	uv run ruff check . --fix

type-check:
	uv run mypy src

pytest:
	uv run pytest

doc-coverage:
	uv run interrogate src

# Line coverage. `--cov=scripts` is not optional and not cosmetic: sonar.sources
# is `src,scripts`, so SonarCloud counts every executable line under scripts/ as
# a line to cover whether or not the report mentions it. Omitting the directory
# does not exclude it — it reports all of it as uncovered, which once put
# new-code coverage at 37.8% on a PR whose script was covered at 99% locally.
# Keep this scope identical to the Pytest step in .github/workflows/ci.yml, or
# the number you read here is not the number the gate reads.
coverage:
	uv run pytest --cov=cgis --cov=scripts --cov-report=term-missing

# Same scope, browsable. Writes htmlcov/ (git-ignored); open htmlcov/index.html.
coverage-html:
	uv run pytest --cov=cgis --cov=scripts --cov-report=html

# Full Python verification (run before every commit/PR)
check: format lint type-check pytest doc-coverage

# ── UI (ui/) ──────────────────────────────────────────────────────────────────

ui-dev:
	cd ui && bun dev

ui-build:
	cd ui && bun run build

ui-test:
	cd ui && bun run test:run

ui-lint:
	cd ui && bun lint

ui-format:
	cd ui && bun run format

ui-preview:
	cd ui && bun run preview

ui-analyze:
	cd ui && bun run analyze

# Full UI verification
ui-check: ui-lint ui-build ui-test

# ── Combined ──────────────────────────────────────────────────────────────────

# Run all checks (Python + UI)
all-checks: check ui-check
