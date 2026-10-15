# Docker needs sudo on some hosts; use it only when the plain client cannot reach the daemon.
DOCKER ?= $(shell docker info >/dev/null 2>&1 && echo docker || echo sudo docker)
COMPOSE := $(DOCKER) compose --env-file .env -f infra/docker-compose.yml
DEV_IMAGE := grocery-deals-agent-dev
DEV_RUN := $(DOCKER) run --rm --user $(shell id -u):$(shell id -g) -e HOME=/tmp \
	--security-opt seccomp=$(CURDIR)/infra/chromium-seccomp.json --init --shm-size 1g \
	-e PYTHONPATH=/repo/backend -v $(CURDIR):/repo -w /repo $(DEV_IMAGE)
DEMO_PLZ ?= 80331
# The address this box is reached at: the server side of the SSH session, else its first IP.
HOST ?= $(or $(word 3,$(SSH_CONNECTION)),$(shell hostname -I | cut -d' ' -f1))
LOCK_FLAGS := --quiet --emit-index-url --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
	--extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

.PHONY: init up down logs lock dev-image lint test check canary \
	models-pull models-lock models-verify models-up models-down obs-up obs-down \
	demo-up demo-prep demo-down

init:  ## create or complete .env and the models directory (runs unprivileged)
	@test -f .env || cp .env.example .env
	@for k in $$(grep -oE '^[A-Z_]+=' .env.example); do \
		grep -q "^$$k" .env || grep "^$$k" .env.example >> .env; done
	@sed -i "s|^VLLM_API_KEY=$$|VLLM_API_KEY=$$(python3 -c 'import secrets; print(secrets.token_hex(24))')|" .env
	@sed -i "s|^MODELS_DIR=$$|MODELS_DIR=$(HOME)/models|" .env
	@for k in LANGFUSE_SALT LANGFUSE_ENCRYPTION_KEY LANGFUSE_NEXTAUTH_SECRET LANGFUSE_DB_PASSWORD \
		CLICKHOUSE_PASSWORD LANGFUSE_REDIS_PASSWORD MINIO_ROOT_PASSWORD LANGFUSE_ADMIN_PASSWORD \
		GRAFANA_ADMIN_PASSWORD; do sed -i "s|^$$k=$$|$$k=$$(python3 -c 'import secrets; print(secrets.token_hex(32))')|" .env; done
	@sed -i "s|^LANGFUSE_PUBLIC_KEY=$$|LANGFUSE_PUBLIC_KEY=pk-lf-$$(python3 -c 'import uuid; print(uuid.uuid4())')|" .env
	@sed -i "s|^LANGFUSE_SECRET_KEY=$$|LANGFUSE_SECRET_KEY=sk-lf-$$(python3 -c 'import uuid; print(uuid.uuid4())')|" .env
	@mkdir -p "$$(sed -n 's/^MODELS_DIR=//p' .env)"
	@echo ".env ready"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

models-pull:  ## download pinned weights from infra/models.txt (NAME= for one model)
	scripts/models.sh pull $(NAME)

models-lock:  ## record checksums of the downloaded weights in infra/models.sha256
	scripts/models.sh lock

models-verify:  ## check downloaded weights against infra/models.sha256 (NAME= for one model)
	scripts/models.sh verify $(NAME)

models-up:  ## start both vLLM servers (GPU 0 and GPU 1) and wait until they are healthy
	$(COMPOSE) --profile models up -d --build --wait llm vlm

models-down:  ## stop the vLLM servers and free the GPUs
	$(COMPOSE) --profile models rm -sf llm vlm

obs-up:  ## start Langfuse (:28736), Grafana (:28737) and Prometheus (127.0.0.1:28738)
	$(COMPOSE) --profile observability up -d --wait langfuse-web langfuse-worker prometheus \
		dcgm-exporter grafana

obs-down:  ## stop the observability stack (its data volumes stay)
	$(COMPOSE) --profile observability stop langfuse-web langfuse-worker langfuse-db clickhouse \
		valkey minio prometheus dcgm-exporter grafana

demo-up: models-up obs-up up demo-prep  ## models, observability and app with warm caches
	@echo "App       http://$(HOST):28735"
	@echo "Langfuse  http://$(HOST):28736  (LANGFUSE_ADMIN_EMAIL / LANGFUSE_ADMIN_PASSWORD in .env)"
	@echo "Grafana   http://$(HOST):28737/d/gda-vllm"

demo-prep:  ## scan DEMO_PLZ once through the API so every chain's leaflets are cached
	@timeout 120 sh -c 'until curl -sf http://127.0.0.1:28734/readyz >/dev/null; do sleep 1; done'
	@curl -sN -X POST http://127.0.0.1:28734/api/scan -H 'Content-Type: application/json' \
		-d '{"location": "$(DEMO_PLZ)"}' | grep -o '"chain": "[a-z]*", "event": "chain_done", "status": "[a-z_]*"'

demo-down: models-down  ## after recording: stop the model servers and free both GPUs

lock:  ## pin runtime and dev Python deps (CPU torch) for the backend image
	uv pip compile pyproject.toml --extra agents --extra scraping --extra observability --extra mcp \
		$(LOCK_FLAGS) -o requirements.lock
	uv pip compile pyproject.toml --extra agents --extra scraping --extra observability --extra mcp \
		--extra dev --extra eval --extra docs \
		$(LOCK_FLAGS) -o requirements-dev.lock

dev-image:
	$(DOCKER) build -q --target dev -t $(DEV_IMAGE) -f infra/backend.Dockerfile . >/dev/null

lint: dev-image
	$(DEV_RUN) sh -c "ruff check --no-cache . && mypy --cache-dir=/dev/null backend/app"

test: dev-image
	$(DEV_RUN) python -m pytest -q -p no:cacheprovider

check: lint test

canary:  ## live check of one chain: make canary CHAIN=penny PLZ=80331 [FORCE=1]
	$(COMPOSE) run --rm --build backend sh -c "alembic upgrade head && \
		python -m app.canary --chain $(CHAIN) --plz $(PLZ) $(if $(FORCE),--force)"
