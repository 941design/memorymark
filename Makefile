.PHONY: up down logs build ext-chrome ext-firefox ext-all clean test dev events help

BRIDGE_URL ?= http://127.0.0.1:21517

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# Docker bridge management
up: ## Start the bridge (docker compose up -d)
	docker compose up -d memorymark-bridge

down: ## Stop the bridge
	docker compose down

logs: ## Tail bridge logs
	docker compose logs -f memorymark-bridge

events: ## Tail SSE event stream (requires curl + jq); override BRIDGE_URL / LAST_ID
	@curl -sN $(if $(LAST_ID),-H "Last-Event-ID: $(LAST_ID)") $(BRIDGE_URL)/api/events | \
		while IFS= read -r line; do \
			case "$$line" in \
				"data: "*) printf '%s\n' "$${line#data: }" | jq . ;; \
				"id: "*|"event: "*|":"*) printf '\033[2m%s\033[0m\n' "$$line" ;; \
				"") printf '\n' ;; \
				*) printf '%s\n' "$$line" ;; \
			esac; \
		done

build: ## Rebuild the Docker image
	docker compose build memorymark-bridge

dev: ## Run bridge locally without Docker (requires uv)
	cd bridge && uv run uvicorn server:app --host 127.0.0.1 --port 21517 --reload

# Extension packaging
DIST_DIR := dist

ext-chrome: $(DIST_DIR)/memorymark-chrome.zip ## Package Chrome extension zip

ext-firefox: $(DIST_DIR)/memorymark-firefox.zip ## Package Firefox extension zip

ext-all: ext-chrome ext-firefox ## Build both extension zips

$(DIST_DIR)/memorymark-chrome.zip:
	@mkdir -p $(DIST_DIR)
	@cd extension && zip -r ../$(DIST_DIR)/memorymark-chrome.zip . \
		-x "*.DS_Store" "manifest.firefox.json"
	@echo "Built $(DIST_DIR)/memorymark-chrome.zip"

$(DIST_DIR)/memorymark-firefox.zip:
	@mkdir -p $(DIST_DIR)
	@# Create temp dir, copy extension, swap manifest
	@rm -rf /tmp/memorymark-firefox-build
	@cp -r extension /tmp/memorymark-firefox-build
	@cp extension/manifest.firefox.json /tmp/memorymark-firefox-build/manifest.json
	@cd /tmp/memorymark-firefox-build && zip -r $(CURDIR)/$(DIST_DIR)/memorymark-firefox.zip . \
		-x "*.DS_Store" "manifest.firefox.json"
	@rm -rf /tmp/memorymark-firefox-build
	@echo "Built $(DIST_DIR)/memorymark-firefox.zip"

# Bridge tests (creates venv automatically via uv)
test: ## Run bridge test suite (44 tests)
	cd bridge && uv run pytest -v

clean: ## Remove build artifacts
	rm -rf $(DIST_DIR)
