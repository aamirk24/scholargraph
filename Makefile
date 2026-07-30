.PHONY: setup db-up db-down migrate dev test test-cov lint clean-cache reset-db

setup: db-up
	uv sync --frozen --no-cache
	uv run alembic upgrade head

db-up:
	@if command -v docker >/dev/null 2>&1; then \
		echo "Starting PostgreSQL with Docker Compose..."; \
		docker compose up -d --wait db; \
	elif pg_isready -h db -p 5432 -U sguser >/dev/null 2>&1; then \
		echo "PostgreSQL is already running in the development environment."; \
	elif pg_isready -h localhost -p 5432 -U sguser >/dev/null 2>&1; then \
		echo "PostgreSQL is already running locally."; \
	else \
		echo "Error: Docker is unavailable and PostgreSQL cannot be reached."; \
		exit 1; \
	fi

db-down:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose down; \
	else \
		echo "Docker is not available inside this container; database lifecycle is managed by Codespaces."; \
	fi

migrate:
	uv run alembic upgrade head

dev: db-up migrate
	bash scripts/dev.sh

test: db-up
	uv run pytest

test-cov: db-up
	uv run pytest \
		--cov=app \
		--cov=crud \
		--cov=routers \
		--cov=services \
		--cov-report=term-missing

lint:
	uv run ruff check .

clean-cache:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	uv cache clean || true

reset-db:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose down -v; \
		docker compose up -d --wait db; \
		uv run alembic upgrade head; \
	else \
		echo "reset-db must be run from an environment with Docker access."; \
		exit 1; \
	fi