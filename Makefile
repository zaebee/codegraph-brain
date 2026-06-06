# 1. Format the code
format:
	uv run ruff format .

# 2. Lint the code (find unused imports, complexity, etc.)
lint:
	uv run ruff check . --fix

# 3. Type check (The most important one!)
type-check:
	uv run mypy src
