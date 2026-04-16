# MemoryMark

> **Alpha — v0.0.1.** First public preview. Expect rough edges; the data format, API, and shortcuts may change before v0.1.

A browser extension that captures web pages into a local [MemPalace](https://pypi.org/project/mempalace/) memory system, with an in-browser Gemma LLM for conversational retrieval.

```
Browser Extension (Chrome / Firefox)
  Content Script        Offscreen Document
  - Readability.js      - Gemma 4 WASM
  - Turndown.js           (MediaPipe + WebGPU)
  - DOM -> Markdown     - Chat + tool-calling
        |                       |
  Overlay UI            Background Worker
  - Capture status      - Bridge HTTP calls
  - Chat interface      - Gemma lifecycle
        |                       |
        +----------+------------+
                   | HTTP localhost:21517
                   v
  Docker Container
  - FastAPI bridge
  - MemPalace (ChromaDB + SQLite)
  - Volume: ~/.mempalace/palace
```

## Features

- **One-key capture** -- Press `Ctrl+Shift+M` (Mac: `Cmd+Shift+M`) to extract the current page as Markdown and store it locally
- **Readability + Turndown** -- Cleans page content with Mozilla Readability, converts to Markdown with headings, lists, code blocks, and tables preserved
- **Duplicate detection** -- Recognizes previously captured URLs instantly
- **Local LLM chat (Chrome only)** -- Gemma 4 runs entirely in-browser via WebGPU (no API keys, no cloud). Requires Chrome's offscreen document API; not available in Firefox.
- **Slash commands** -- `/summarize`, `/search <query>`, `/status` work in all browsers
- **Tool-augmented chat (Chrome only)** -- Gemma searches your saved pages to answer free-form questions
- **Cross-browser capture** -- Page capture and slash commands work in Chrome and Firefox. LLM chat requires Chrome with WebGPU.

## Prerequisites

- Docker and Docker Compose
- Chrome 116+ or Firefox 109+
- A GPU with WebGPU + `shader-f16` support (for Gemma chat; capture works without it)

## Quick Start

### 1. Start the bridge

```bash
make up
```

This runs the FastAPI bridge on `localhost:21517` wrapping MemPalace in Docker.

### 2. Load the extension

**Chrome:**
1. Navigate to `chrome://extensions`
2. Enable Developer Mode
3. Click "Load unpacked" and select the `extension/` directory

**Firefox:**
1. Navigate to `about:debugging#/runtime/this-firefox`
2. Click "Load Temporary Add-on"
3. Select `extension/manifest.firefox.json`

### 3. Capture a page

Navigate to any article and press `Ctrl+Shift+M`. The overlay confirms the save.

### 4. Chat

Click "Open Chat" in the overlay or press the shortcut again while the overlay is visible. Ask questions about your saved pages, or use:

- `/summarize` -- Summarize the current page
- `/search machine learning` -- Search across all saved pages
- `/status` -- Show MemPalace statistics

## Project Structure

```
memorymark/
  bridge/
    server.py              # FastAPI REST API wrapping MemPalace
    Dockerfile             # Python 3.11-slim container
    requirements.txt       # mempalace, fastapi, uvicorn
    conftest.py            # Pytest fixtures (isolated test palace)
    test_server.py         # Health + CORS tests
    test_write_endpoints.py  # Drawer CRUD tests
    test_read_endpoints.py   # Search, status, taxonomy, by-url tests
  extension/
    manifest.json          # Chrome MV3 manifest
    manifest.firefox.json  # Firefox MV3 manifest
    background.js          # Service worker: capture flow, bridge calls, Gemma lifecycle
    content.js             # Content script: DOM extraction, overlay, chat UI
    content.css            # Overlay and chat styles
    offscreen.html         # Gemma host document
    offscreen.js           # MediaPipe + WebGPU inference, tool execution
    icons/                 # Extension icons (16, 48, 128px)
    lib/
      readability.min.js   # Mozilla Readability (~90KB)
      turndown.min.js      # Turndown HTML-to-Markdown (~27KB)
      turndown-plugin-gfm.min.js  # GFM tables + strikethrough (~4KB)
      mediapipe/           # MediaPipe Tasks GenAI WASM runtime
  docker-compose.yml       # Bridge service definition
  Makefile                 # Build and management targets
```

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make up` | Start the bridge (`docker compose up -d`) |
| `make down` | Stop the bridge |
| `make logs` | Tail bridge logs |
| `make build` | Rebuild the Docker image |
| `make dev` | Run bridge locally without Docker (requires uv) |
| `make test` | Run bridge test suite (44 tests) |
| `make ext-chrome` | Package Chrome extension zip |
| `make ext-firefox` | Package Firefox extension zip |
| `make ext-all` | Build both extension zips |
| `make events` | Tail SSE event stream (requires curl + jq) |
| `make clean` | Remove build artifacts |
| `make help` | Show all available targets |

## Bridge API

The bridge runs on `localhost:21517` and exposes:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check with drawer count |
| `POST` | `/api/drawers` | Create a drawer |
| `PUT` | `/api/drawers/{id}` | Update a drawer |
| `DELETE` | `/api/drawers/{id}` | Delete a drawer |
| `GET` | `/api/search` | Semantic search |
| `GET` | `/api/status` | Palace statistics |
| `GET` | `/api/taxonomy` | Wing/room hierarchy |
| `GET` | `/api/wings` | List wings |
| `GET` | `/api/rooms` | List rooms |
| `GET` | `/api/drawers/{id}` | Get drawer by ID (full content) |
| `GET` | `/api/drawers/by-url` | Duplicate detection by URL |
| `GET` | `/api/events` | SSE event stream ([docs](docs/sse-events.md)) |

## MCP Integration

The bridge is a unified local memory daemon. Browser clients (the extension) talk to it over REST at `/api/*`; AI agent harnesses talk to it over MCP Streamable HTTP at `/mcp`. One process owns the palace, so everything — captures from the web, tool calls from agents — writes to the same memory. Just make sure `make up` is running.

MCP endpoint: `http://localhost:21517/mcp`

### Claude Desktop

Claude Desktop does not yet speak Streamable HTTP natively, so the connection is bridged through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote). Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "memorymark": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:21517/mcp"]
    }
  }
}
```

Restart Claude Desktop. Requires Node.js on `PATH`.

### Claude Code

```bash
claude mcp add memorymark http://localhost:21517/mcp --transport http
```

Flags must come **after** the positional `<name> <url>` arguments. Add `--scope user` to make the server available in every project, or `--scope project` to commit it to a shared `.mcp.json`. Verify with `claude mcp list`.

### Codex

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.memorymark]
url = "http://localhost:21517/mcp"
```

Restart Codex to pick up the change.

### Other harnesses

Any MCP client supporting Streamable HTTP works the same way — point it at `http://localhost:21517/mcp`. See [docs/mcp-integration.md](docs/mcp-integration.md) for the full tool reference (24 tools: search, drawer CRUD, knowledge graph, diary, and more).

### Inspecting with MCP Inspector

The official [`@modelcontextprotocol/inspector`](https://github.com/modelcontextprotocol/inspector) is the quickest way to poke at the bridge interactively or from a script.

**UI mode** — opens a browser panel for live exploration:

```bash
npx @modelcontextprotocol/inspector
```

Then in the UI: set Transport to **Streamable HTTP**, URL to `http://localhost:21517/mcp`, click **Connect**. You can list tools, call them with JSON arguments, and see responses.

**CLI mode** — non-interactive, handy for scripts and CI:

```bash
# List all tools
npx @modelcontextprotocol/inspector --cli http://localhost:21517/mcp \
  --transport http --method tools/list

# Call a tool
npx @modelcontextprotocol/inspector --cli http://localhost:21517/mcp \
  --transport http --method tools/call \
  --tool-name mempalace_status

# Semantic search
npx @modelcontextprotocol/inspector --cli http://localhost:21517/mcp \
  --transport http --method tools/call \
  --tool-name mempalace_search \
  --tool-arg query="machine learning" --tool-arg limit=3
```

Requires `make up` to be running first.

## Configuration

Settings stored in `chrome.storage.local`:

| Setting | Default | Description |
|---------|---------|-------------|
| `bridgeUrl` | `http://localhost:21517` | Bridge endpoint |
| `autoClose` | `4000` | Overlay auto-dismiss (ms) |
| `gemmaEnabled` | `true` | Enable Gemma download |

## Gemma Model

The extension uses **Gemma 4 E2B IT** (`gemma-4-E2B-it-web.task`, ~2GB, Apache 2.0) from `litert-community/gemma-4-E2B-it-litert-lm` on HuggingFace. This is Google's edge-optimized 2B-parameter model in MediaPipe `.task` format.

On first install, the model is downloaded and cached in the browser's Origin Private File System (OPFS). Download progress is shown on the extension badge. On subsequent launches the cached model loads directly. The model runs entirely on-device via WebGPU -- no data leaves your machine.

Chat uses the Gemma 4 prompt format (`<|turn>`/`<turn|>` tags) with native tool-call support (`<|tool_call>`). Every chat message also pre-fetches relevant context from the bridge so the model always has grounding material.

If WebGPU with `shader-f16` is unavailable, capture and slash commands still work; only free-form chat is disabled.

**Known issue**: Gemma 4 E2B may crash during WASM initialization on Apple M4 (non-Max) chips due to an [open MediaPipe bug](https://github.com/google-ai-edge/mediapipe/issues/6270). M4 Max and other hardware work fine.

## Privacy & Data Location

MemoryMark is designed to keep everything on your machine.

- **Captured pages** live in `./data/mempalace/` (a ChromaDB + SQLite store), mounted into the bridge container from the host.
- **The Gemma model** is downloaded once and cached in your browser's Origin Private File System (OPFS).
- **No telemetry, no accounts, no cloud calls.** The extension only talks to `http://localhost:21517`. Gemma runs on-device via WebGPU.
- To wipe everything, stop the bridge (`make down`), delete `./data/`, and clear the extension's site storage.

## Running Tests

```bash
make test
```

Runs the pytest suite against the FastAPI bridge using an isolated in-memory MemPalace instance. Requires [uv](https://docs.astral.sh/uv/).

## License

[MIT](LICENSE)
