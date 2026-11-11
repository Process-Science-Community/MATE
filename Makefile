.DEFAULT_GOAL := help
.PHONY: help install check-env preflight dev dev-api dev-web lite lite-reset e2e-studio module-new module-validate module-test up up-dev down build test typecheck fmt clean codegen docs deploy

# Tab indentation is required for Make recipes.

# Dev API port. Override with `make dev PORT=8001`.
PORT ?= 8000
# Lite dev mode (`make lite`): web port and the module allowlist (comma-separated ids).
WEB_PORT ?= 3000
MODULES ?=

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nMate – common tasks\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Resolve all deps (uv + pnpm)
	uv sync --extra dev
	pnpm install

check-env: ## Validate the root environment file before starting Docker
	@sh ./scripts/check-local-env.sh true

preflight: ## Validate local env, free $(PORT), and reap stale workers
	@sh ./scripts/check-local-env.sh
	@pids=$$(lsof -nP -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pids" ]; then echo "preflight: freeing :$(PORT) (pids $$pids)"; kill -9 $$pids 2>/dev/null || true; fi
	@pkill -9 -f "uvicorn mate.api.main" 2>/dev/null || true
	@pkill -9 -f "$(CURDIR)/.venv/bin/python -c from multiprocessing" 2>/dev/null || true
	@true

dev: preflight ## Run the API and the web dev server together (no docker)
	uv run alembic -c apps/api/alembic.ini upgrade head
	@set -a; . ./.env; set +a; \
	trap 'kill 0' INT; \
	(uv run uvicorn mate.api.main:app --reload --reload-dir apps/api/src --reload-dir packages/module-sdk-py/src --app-dir apps/api/src --host 127.0.0.1 --port $(PORT) --timeout-graceful-shutdown 3) & \
	(cd apps/web && pnpm dev) & \
	wait

dev-api: preflight ## Run only the API (with --reload)
	uv run alembic -c apps/api/alembic.ini upgrade head
	uv run uvicorn mate.api.main:app --reload --reload-dir apps/api/src --reload-dir packages/module-sdk-py/src --app-dir apps/api/src --host 127.0.0.1 --port $(PORT) --timeout-graceful-shutdown 3

dev-web: preflight ## Run only the web dev server
	set -a; . ./.env; set +a; cd apps/web && pnpm dev

lite: ## Low-memory native stack, no .env needed: make lite [MODULES=a,b] [PORT=] [WEB_PORT=]
	@MODULES="$(MODULES)" PORT="$(PORT)" WEB_PORT="$(WEB_PORT)" bash ./scripts/lite.sh

lite-reset: ## Wipe the lite stack's data (data/lite only)
	rm -rf data/lite

e2e-studio: ## Playwright check that the Studio matches the module page (scaffolds a throwaway module)
	bash scripts/e2e-studio.sh

module-new: ## Scaffold modules/<ID>: make module-new ID=my_module [PANEL=1]
	@test -n "$(ID)" || { echo "usage: make module-new ID=my_module [PANEL=1]"; exit 2; }
	uv run --extra dev mate-module new "$(ID)" $(if $(filter 1 yes true,$(PANEL)),--panel)

module-validate: ## Validate every module (manifest, HOW_TO_USE, layout.json)
	uv run --extra dev mate-module validate

module-test: ## Run one module's tests: make module-test ID=my_module
	@test -n "$(ID)" || { echo "usage: make module-test ID=my_module"; exit 2; }
	uv run --extra dev mate-module test "$(ID)"

up: check-env ## docker compose up (production-style images)
	docker compose up -d --build

up-dev: check-env ## docker compose with the dev overlay (uvicorn --reload + next dev)
	docker compose -f docker-compose.yml -f compose.dev.yml up --build

down: ## Stop the compose stack
	docker compose down

build: ## Build both Docker images
	docker compose build

test: ## Run the Python test suite
	uv run --extra dev pytest apps/api/tests -v

sdk-jvm: ## Build the JVM module SDK + example + conformance jars (needs JDK 17+)
	cd packages/module-sdk-jvm && ./gradlew build shadowJar :examples:performance-metrics:copyExampleJar

typecheck: ## Type-check the web app
	cd apps/web && pnpm typecheck

fmt: ## Format Python with ruff
	uv run ruff check --fix .
	uv run ruff format .

codegen: ## Regenerate TS types from the running API's /openapi.json
	cd apps/web && pnpm codegen

docs: ## Rebuild the landing page's documentation site from landing/docs/content/*.md
	node landing/docs/build.mjs

deploy: ## Push + redeploy to the uni VM (run on the FB4-DEV-VPN)
	./scripts/deploy.sh

clean: ## Wipe local data + Keycloak Postgres volume – irrevocable
	rm -rf data/event_logs/* data/module_results/* data/users data/metadata.db data/metadata.db-wal data/metadata.db-shm data/.multi_user_migrated
	-docker volume rm flows-and-funds_kc-data 2>/dev/null || true
	@echo "data/ wiped and Keycloak volume removed. Module folders under modules/ are kept."
