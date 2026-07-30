.PHONY: up down logs build ext-chrome ext-firefox ext-all clean test dev events help \
        install uninstall launchd-status launchd-logs launchd-plist

BRIDGE_URL ?= http://127.0.0.1:21517

# launchd deployment (macOS host). Runs the bridge natively under launchd
# instead of Docker; the two are mutually exclusive because both bind
# BRIDGE_PORT. PALACE_PATH defaults to the same directory docker-compose.yml
# bind-mounts, so switching supervisors does not move the palace.
BRIDGE_HOST ?= 127.0.0.1
BRIDGE_PORT ?= 21517
PALACE_PATH ?= $(CURDIR)/data/mempalace
LOG_DIR ?= $(HOME)/Library/Logs/memorymark
LAUNCH_AGENTS_DIR ?= $(HOME)/Library/LaunchAgents
LAUNCHD_LABEL ?= design.941.memorymark.bridge
LAUNCHD_PLIST := $(LAUNCH_AGENTS_DIR)/$(LAUNCHD_LABEL).plist
UV ?= $(shell command -v uv)

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

# launchd management (macOS host, native process -- not Docker)
launchd-plist: ## Print the generated plist without installing it
	@cd bridge && $(UV) run --no-project python install_launchd.py --print-only \
		--uv "$(UV)" --project-dir "$(CURDIR)" --palace-path "$(PALACE_PATH)" \
		--log-dir "$(LOG_DIR)" --host "$(BRIDGE_HOST)" --port "$(BRIDGE_PORT)" \
		--label "$(LAUNCHD_LABEL)"

install: ## Install and start the bridge as a launchd agent (macOS)
	@[ "$$(uname -s)" = "Darwin" ] || { echo "install: launchd is macOS-only (found $$(uname -s))" >&2; exit 1; }
	@[ -n "$(UV)" ] || { echo "install: uv not found on PATH" >&2; exit 1; }
	@if [ -n "$$(docker compose ps -q memorymark-bridge 2>/dev/null)" ]; then \
		echo "install: the Docker bridge is running and owns port $(BRIDGE_PORT)." >&2; \
		echo "         Run 'make down' first -- launchd and Docker cannot both serve the bridge." >&2; \
		exit 1; \
	fi
	@if lsof -nP -iTCP:$(BRIDGE_PORT) -sTCP:LISTEN >/dev/null 2>&1 && \
	    ! launchctl print "gui/$$(id -u)/$(LAUNCHD_LABEL)" >/dev/null 2>&1; then \
		echo "install: something already listens on port $(BRIDGE_PORT):" >&2; \
		lsof -nP -iTCP:$(BRIDGE_PORT) -sTCP:LISTEN >&2; \
		exit 1; \
	fi
	@mkdir -p "$(PALACE_PATH)" "$(LOG_DIR)"
	cd bridge && $(UV) sync --frozen
	cd bridge && $(UV) run --no-project python install_launchd.py \
		--uv "$(UV)" \
		--project-dir "$(CURDIR)" \
		--palace-path "$(PALACE_PATH)" \
		--log-dir "$(LOG_DIR)" \
		--host "$(BRIDGE_HOST)" \
		--port "$(BRIDGE_PORT)" \
		--label "$(LAUNCHD_LABEL)" \
		--launch-agents-dir "$(LAUNCH_AGENTS_DIR)"
	uid="$$(id -u)"; \
	domain="gui/$$uid"; \
	service="$$domain/$(LAUNCHD_LABEL)"; \
	bootstrap_log="$$(mktemp -t memorymark-launchctl.XXXXXX)"; \
	trap 'rm -f "$$bootstrap_log"' EXIT; \
	launchctl bootout "$$service" >/dev/null 2>&1 || launchctl bootout "$$domain" "$(LAUNCHD_PLIST)" >/dev/null 2>&1 || true; \
	status=0; \
	for attempt in 1 2 3 4 5; do \
		if launchctl bootstrap "$$domain" "$(LAUNCHD_PLIST)" >"$$bootstrap_log" 2>&1; then \
			status=0; \
			break; \
		fi; \
		status="$$?"; \
		launchctl bootout "$$service" >/dev/null 2>&1 || launchctl bootout "$$domain" "$(LAUNCHD_PLIST)" >/dev/null 2>&1 || true; \
		sleep "$$attempt"; \
	done; \
	if [ "$$status" -ne 0 ]; then \
		cat "$$bootstrap_log" >&2; \
		launchctl print "$$service" || true; \
		exit "$$status"; \
	fi; \
	launchctl kickstart -k "$$service"
	@printf 'Waiting for bridge health'; \
	for attempt in $$(seq 1 20); do \
		if curl -fsS -m 2 "http://$(BRIDGE_HOST):$(BRIDGE_PORT)/api/health" >/dev/null 2>&1; then \
			printf ' ok\n'; \
			echo "Installed launchd agent: $(LAUNCHD_LABEL)"; \
			echo "  plist:  $(LAUNCHD_PLIST)"; \
			echo "  palace: $(PALACE_PATH)"; \
			echo "  logs:   $(LOG_DIR)/bridge.{out,err}.log"; \
			exit 0; \
		fi; \
		printf '.'; sleep 1; \
	done; \
	printf '\n'; \
	echo "install: agent loaded but /api/health stayed silent for 20s -- check 'make launchd-logs'" >&2; \
	exit 1

uninstall: ## Stop and remove the launchd agent (palace data is left untouched)
	@[ "$$(uname -s)" = "Darwin" ] || { echo "uninstall: launchd is macOS-only" >&2; exit 1; }
	@launchctl bootout "gui/$$(id -u)/$(LAUNCHD_LABEL)" 2>/dev/null || true
	rm -f "$(LAUNCHD_PLIST)"
	@echo "Removed launchd agent: $(LAUNCHD_LABEL)"
	@echo "Palace left in place: $(PALACE_PATH)"

launchd-status: ## Show launchd agent state and bridge health
	@launchctl print "gui/$$(id -u)/$(LAUNCHD_LABEL)" 2>/dev/null | \
		grep -E "state = |pid = |last exit code|program = " || \
		echo "$(LAUNCHD_LABEL): not loaded"
	@curl -fsS -m 2 "http://$(BRIDGE_HOST):$(BRIDGE_PORT)/api/health" && printf '\n' || \
		echo "bridge: not answering on $(BRIDGE_HOST):$(BRIDGE_PORT)"

launchd-logs: ## Tail the launchd agent logs
	@mkdir -p "$(LOG_DIR)"
	tail -q -F "$(LOG_DIR)/bridge.out.log" "$(LOG_DIR)/bridge.err.log"

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
test: ## Run bridge test suite
	cd bridge && uv run pytest -v

clean: ## Remove build artifacts
	rm -rf $(DIST_DIR)
