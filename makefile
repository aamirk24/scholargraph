.PHONY: setup db-up db-down migrate dev test test-cov lint clean-cache reset-db

setup: db-up
	uv sync --frozen --no-cache
	uv run alembic upgrade head

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

dev: db-up migrate
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test: db-up
	uv run pytest

test-cov: db-up
	uv run pytest --cov=app --cov=crud --cov=routers --cov=services --cov-report=term-missing

lint:
	uv run ruff check .

clean-cache:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	uv cache clean || true

reset-db:
	docker compose down -v
	docker compose up -d --wait db
	uv run alembic upgrade head