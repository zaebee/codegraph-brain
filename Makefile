.PHONY: format lint type-check pytest doc-coverage check \
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
