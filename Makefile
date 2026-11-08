.PHONY: help api test lint compose-config up down preflight

help:
	@echo "Targets: api test lint compose-config up down preflight"

api:
	uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

test:
	pytest -q

lint:
	ruff check backend tests

compose-config:
	docker compose config

up:
	docker compose up --build

down:
	docker compose down

preflight:
	bash infra/scripts/preflight.sh
