.PHONY: up down logs build ext-chrome ext-firefox ext-all clean test

# Docker bridge management
up:
	docker compose up -d memorymark-bridge

down:
	docker compose down

logs:
	docker compose logs -f memorymark-bridge

build:
	docker compose build memorymark-bridge

# Extension packaging
DIST_DIR := dist

ext-chrome: $(DIST_DIR)/memorymark-chrome.zip

ext-firefox: $(DIST_DIR)/memorymark-firefox.zip

ext-all: ext-chrome ext-firefox

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
test:
	cd bridge && uv run pytest -v

clean:
	rm -rf $(DIST_DIR)
