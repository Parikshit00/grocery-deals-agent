# Docker needs sudo on some hosts; use it only when the plain client cannot reach the daemon.
DOCKER ?= $(shell docker info >/dev/null 2>&1 && echo docker || echo sudo docker)
COMPOSE := $(DOCKER) compose --env-file .env -f infra/docker-compose.yml
DEV_IMAGE := grocery-deals-agent-dev
DEV_RUN := $(DOCKER) run --rm --user $(shell id -u):$(shell id -g) -e HOME=/tmp \
	-e PYTHONPATH=/repo/backend -v $(CURDIR):/repo -w /repo $(DEV_IMAGE)
LOCK_FLAGS := --quiet --emit-index-url --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
	--extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

.PHONY: init up down logs lock dev-image lint test check

init:  ## create or complete .env and the models directory (runs unprivileged)
	@test -f .env || cp .env.example .env
	@for k in $$(grep -oE '^[A-Z_]+=' .env.example); do \
		grep -q "^$$k" .env || grep "^$$k" .env.example >> .env; done
	@sed -i "s|^VLLM_API_KEY=$$|VLLM_API_KEY=$$(python3 -c 'import secrets; print(secrets.token_hex(24))')|" .env
	@sed -i "s|^MODELS_DIR=$$|MODELS_DIR=$(HOME)/models|" .env
	@mkdir -p "$$(sed -n 's/^MODELS_DIR=//p' .env)"
	@echo ".env ready"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

lock:  ## pin runtime and dev Python deps (CPU torch) for the backend image
	uv pip compile pyproject.toml --extra agents --extra scraping $(LOCK_FLAGS) -o requirements.lock
	uv pip compile pyproject.toml --extra agents --extra scraping --extra dev $(LOCK_FLAGS) \
		-o requirements-dev.lock

dev-image:
	$(DOCKER) build -q --target dev -t $(DEV_IMAGE) -f infra/backend.Dockerfile . >/dev/null

lint: dev-image
	$(DEV_RUN) sh -c "ruff check --no-cache . && mypy --cache-dir=/dev/null backend/app"

test: dev-image
	$(DEV_RUN) python -m pytest -q -p no:cacheprovider

check: lint test
