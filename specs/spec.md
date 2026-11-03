# MemoryMark — Specification

> **Scope**: Currently implemented functionality in MemoryMark v0.0.1. Future requirements live in separate `specs/<feature>-spec.md` files. The original design journal is at `specs/sorted-imagining-journal.md`.

---

## 1. Purpose & Scope

MemoryMark is a browser extension that captures web pages into a local, LLM-searchable memory system called MemPalace. It solves three problems:

1. Bookmarks lose content — MemoryMark stores the full page as clean Markdown
2. Cloud clippers raise privacy concerns — all storage and inference run locally
3. Search is keyword-only elsewhere — MemPalace uses vector embeddings for semantic retrieval

**In scope (v0.0.1)**:
- One-keystroke capture of any web page to local storage
- Duplicate detection by URL
- In-browser conversational retrieval via Gemma 4 (Chrome only)
- Slash commands for search and summarization (Chrome + Firefox)
- REST API for browser extension access
- MCP endpoint for AI agent access (Claude Code, Cursor, Codex, etc.)

**Out of scope**: configurable wing selection, custom room mapping, auto-categorization, bulk capture, capture history popup, knowledge graph extraction (see separate feature specs).

---

## 2. Architecture Overview

```
Browser Extension (Chrome MV3 / Firefox MV3)
  Content Script              Offscreen Document (Chrome only)
  ┌──────────────────┐        ┌────────────────────────────┐
  │ Readability.js   │        │ Gemma 4 E2B WASM           │
  │ Turndown.js      │        │ MediaPipe + WebGPU          │
  │ DOM → Markdown   │        │ Chat + tool-calling         │
  └────────┬─────────┘        └──────────────┬─────────────┘
           │                                 │
  Overlay UI (injected)       Background Service Worker
  ┌──────────────────┐        ┌────────────────────────────┐
  │ Capture feedback │◄───────│ Bridge HTTP calls           │
  │ Chat interface   │        │ Gemma lifecycle management  │
  └──────────────────┘        └──────────────┬─────────────┘
                                             │ HTTP (localhost only)
                                             ▼
Docker Container (port 21517, loopback-only)
  ┌──────────────────────┐  ┌──────────────────────────┐
  │ FastAPI bridge       │──│ MemPalace                │
  │ REST  /api/*         │  │ ChromaDB (vector search) │
  │ MCP   /mcp           │  │ SQLite (knowledge graph) │
  └──────────────────────┘  └──────────────────────────┘
  Volume: ./data/mempalace (persistent)
```

**Key design decisions**:
- The bridge is the sole writer to MemPalace; the extension never touches storage directly
- REST (`/api/*`) serves the browser extension; MCP (`/mcp`) serves AI agents — one process, one palace
- Content is stored verbatim (no summarization at capture time)
- All drawers use default wing `Web` and room `Captures` in v1

---

## 3. Core Functionality

### 3.1 Page Capture

Triggered by `Ctrl+Shift+M` (Mac: `Cmd+Shift+M`).

Pipeline:
1. Background service worker receives the keyboard command
2. Content script clones DOM, runs Readability to extract article, runs Turndown to convert to Markdown
3. Metadata extracted from `<meta>` tags: title, author, date, description, source URL
4. Background worker calls `GET /api/drawers/by-url` — if already captured, shows "Already in MemPalace"
5. If new: calls `POST /api/drawers` with wing `Web`, room `Captures`, hall `hall_discoveries`
6. Overlay shows feedback state: Saving → Saved / Already exists / Error / Bridge unreachable
7. Auto-dismisses after 4 seconds (configurable via `autoClose` setting)

Content format stored in MemPalace:
```markdown
# {title}

> Source: {source_url}
> Author: {author} | Date: {date}

{Markdown body from Readability + Turndown}
```

Content is capped at 200,000 characters; oversized pages are truncated with a warning.

### 3.2 Duplicate Detection

Every capture begins with a URL lookup. The extension badge shows a checkmark (`✓`) on tab activation when the current URL matches a stored page.

- Endpoint: `GET /api/drawers/by-url?url=<url>`
- Match: exact on `source_url` metadata field
- Response: `{ "exists": true, "drawer_id": "...", "title": "..." }` or `{ "exists": false }`

### 3.3 In-Browser Chat (Chrome only)

Chat requires Chrome with WebGPU + `shader-f16` support. Falls back gracefully (slash commands still work).

**Model**: Gemma 4 E2B IT (`gemma-4-E2B-it-web.task`, ~2GB, Apache 2.0), downloaded to OPFS on first install. Badge shows download progress (e.g., "47%").

**Chat UI**: Slide-in panel (≈400×500px) on the right edge of the viewport. Opened via "Open Chat" button in the overlay, or second shortcut press.

**Slash commands** (Chrome and Firefox):
- `/summarize` — fetches stored content for the current URL and generates a summary
- `/search <query>` — semantic search across all stored pages; returns titles and URLs
- `/status` — shows total drawer count and wing list

**Free-form questions** (Chrome only): Gemma uses tool-calling to search memory before answering. Responses stream token-by-token with a running token count and elapsed time.

**Gemma tools**:
| Tool | Description | Bridge Endpoint |
|------|-------------|-----------------|
| `search_memory` | Semantic search across stored pages | `GET /api/search` |
| `get_page_content` | Retrieve full stored content for a URL | `GET /api/drawers/by-url` |

### 3.4 MCP Agent Access

The bridge exposes the full MemPalace tool surface at `POST /mcp` (Streamable HTTP). Any MCP-compatible agent harness can call it. Agents and the browser extension share the same palace — a page captured by the extension is immediately visible to MCP tool calls, and vice versa.

---

## 4. Security Model

- Bridge binds to `127.0.0.1:21517` — not accessible outside localhost
- CORS: allows only `chrome-extension://*` and `moz-extension://*` origins; rejects `localhost:*` and `127.0.0.1:*` (prevents local web apps from reading the palace)
- No authentication in v1 (single-user, local-only)
- No telemetry, no cloud calls during normal operation; model downloads from HuggingFace on first install only
- Extension host permissions restricted to `http://localhost:21517/*`

---

## 5. Data Model

**Drawer ID**: `drawer_{wing}_{room}_{sha256(wing+room+content)[:24]}` — content-addressed, enables idempotent upserts.

**Drawer fields**:
```
drawer_id    string   Content-addressed ID
content      string   Full Markdown text (max 100K chars)
wing         string   "Web" (fixed in v1)
room         string   "Captures" (fixed in v1)
title        string   Page title
source_url   string   Original URL (used for duplicate detection)
metadata     object   {wing, room, source_url, title, hall, timestamp, added_by, filed_at, chunk_index}
```

MemPalace defaults for v1: `wing="Web"`, `room="Captures"`, `hall="hall_discoveries"`.

---

## 6. REST API Reference

Base URL: `http://localhost:21517`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Status check: `{ status, drawers }` |
| `POST` | `/api/drawers` | Create drawer (idempotent by content hash) |
| `PUT` | `/api/drawers/{id}` | Update drawer content or metadata |
| `DELETE` | `/api/drawers/{id}` | Delete drawer |
| `GET` | `/api/drawers/{id}` | Get full drawer by ID |
| `GET` | `/api/drawers/by-url` | Lookup by `source_url` metadata (`url` param) |
| `GET` | `/api/search` | Semantic search (`query`, `wing?`, `room?`, `limit?`) |
| `GET` | `/api/status` | Palace statistics |
| `GET` | `/api/taxonomy` | Wing/room hierarchy |
| `GET` | `/api/wings` | List wings with drawer counts |
| `GET` | `/api/rooms` | List rooms (`wing?` filter) |
| `POST` | `/mcp` | MCP Streamable HTTP endpoint |

---

## 7. Extension Configuration

Settings persisted in `chrome.storage.local`:

| Key | Default | Description |
|-----|---------|-------------|
| `bridgeUrl` | `http://localhost:21517` | Bridge endpoint |
| `autoClose` | `4000` | Overlay auto-dismiss delay (ms) |
| `gemmaEnabled` | `true` | Whether to download and enable Gemma |

Runtime-only (set by extension code, not user-configurable):
- `gemmaAvailable` — `true` after successful model init
- `gemmaError` — human-readable error if model init fails

---

## 8. Cross-Browser Compatibility

| Feature | Chrome 116+ | Firefox 109+ |
|---------|------------|--------------|
| Page capture | Yes | Yes |
| Duplicate detection | Yes | Yes |
| Overlay feedback | Yes | Yes |
| Slash commands | Yes | Yes |
| Free-form Gemma chat | Yes (WebGPU required) | No (no offscreen document API) |
| Badge progress indicator | Yes | Yes |

Firefox uses a persistent background page instead of Chrome's offscreen document API. WebGPU must be manually enabled in Firefox (`dom.webgpu.enabled`); Gemma is skipped gracefully if unavailable.

---

## 9. Error Handling

| Scenario | Behavior |
|----------|----------|
| Bridge unreachable | Overlay shows "MemPalace bridge not running" with `docker compose up -d` instruction |
| Readability fails | Falls back to `document.body.innerHTML` through Turndown (captures full page) |
| Gemma model download fails | Retry with exponential backoff; badge shows error; chat disabled until resolved |
| WebGPU unavailable | Skip Gemma entirely; capture works; chat shows "Gemma requires WebGPU" |
| MemPalace write fails | Overlay shows error + retry button; content held in memory until retry or dismiss |
| Content >100K chars | Truncated to limit; overlay shows truncation warning |

---

## 10. Non-Functional Requirements

- **Privacy**: No data leaves localhost during normal operation
- **Offline-capable**: Captured pages and Gemma model are local; retrieval works without internet
- **Idempotent capture**: Re-capturing the same page produces no duplicate (content-addressed IDs)
- **Content fidelity**: Markdown preserves headings, lists, code blocks, tables, and links
- **Graceful degradation**: Capture and slash commands work without WebGPU
- **Extension size**: ~100KB gzipped (excluding Gemma model)

---

## 11. Acceptance Criteria

- `GET /api/health` returns `{ "status": "ok", "drawers": <count> }`
- `POST /api/drawers` with same content twice returns the same `drawer_id` (idempotent)
- `GET /api/drawers/by-url?url=<url>` returns `{ "exists": true }` after capturing that URL
- Pressing `Ctrl+Shift+M` on a new page shows "Saving…" then "Saved" overlay
- Pressing `Ctrl+Shift+M` on a previously captured page shows "Already in MemPalace"
- `/summarize` returns a summary only if the current page has been captured
- `/search <query>` returns matching pages with titles and source URLs
- MCP `tools/list` call at `/mcp` returns all MemPalace tools
- All 44 bridge tests pass: `make test`
- CORS rejects requests from `http://localhost:*` origins
