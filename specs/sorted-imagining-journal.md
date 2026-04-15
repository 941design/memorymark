# MemoryMark — Full Specification

A browser extension that captures web pages into a local MemPalace memory system via keyboard shortcut, with an in-browser Gemma LLM for conversational retrieval.

---

## 1. Context

**Problem**: Web research is ephemeral. Bookmarks lack content, copy-paste loses structure, and cloud-based clippers raise privacy concerns. There is no local-first, LLM-searchable system for capturing and querying web content.

**Solution**: MemoryMark — a browser extension that converts the current page to Markdown and stores it verbatim in MemPalace (a local vector-backed memory system). A browser-local Gemma LLM provides conversational access to stored memories via an overlay chat.

**Key principles**:
- Capture is verbose and verbatim — no summarization at capture time
- Everything runs locally — MemPalace in Docker, Gemma in-browser via WebGPU
- The MemPalace HTTP bridge is a shared service usable by other applications

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser Extension (Chrome + Firefox, Manifest V3)           │
│                                                              │
│  Content Script              Offscreen Document              │
│  ┌────────────────┐          ┌─────────────────────┐        │
│  │ Readability.js  │          │ Gemma 4 WASM        │        │
│  │ + Turndown.js   │          │ (MediaPipe + WebGPU) │        │
│  │ DOM → Markdown  │          │ Chat + Tool-calling  │        │
│  └───────┬────────┘          └──────────┬──────────┘        │
│          │                              │                    │
│  Overlay UI (injected)       Background Service Worker       │
│  ┌────────────────┐          ┌─────────────────────┐        │
│  │ Capture status  │◄────────│ HTTP calls to bridge │        │
│  │ Chat interface  │         │ Gemma lifecycle mgmt │        │
│  │ Visual feedback │         │ Shortcut handling    │        │
│  └────────────────┘          └──────────┬──────────┘        │
└──────────────────────────────────────────┼──────────────────┘
                                           │ HTTP (localhost)
                                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Docker Container                                            │
│  ┌───────────────────────┐  ┌────────────────────────────┐  │
│  │ FastAPI HTTP Bridge    │  │ MemPalace                  │  │
│  │ REST endpoints         │──│ ChromaDB (vector search)   │  │
│  │ localhost:21517        │  │ SQLite (knowledge graph)   │  │
│  └───────────────────────┘  └────────────────────────────┘  │
│                                                              │
│  Volume: ~/.mempalace/palace (persistent)                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Components

### 3.1 Docker Container — MemPalace + HTTP Bridge

**Purpose**: Run MemPalace and expose it over HTTP so the browser extension (and other local applications) can interact with it.

**Contents**:
- Python 3.11+ base image
- MemPalace (`pip install mempalace`)
- FastAPI + Uvicorn HTTP server
- Single container, single process (Uvicorn serves FastAPI)

**Persistent storage**: `~/.mempalace/palace` mounted as a Docker volume.

**Port**: `21517` on localhost (configurable via env var `MEMORYMARK_PORT`).

**Startup**:
```bash
docker compose up -d memorymark-bridge
```

#### 3.1.1 FastAPI Bridge Endpoints

The bridge wraps MemPalace's Python API directly (imports `mempalace` as a library, does NOT shell out to the MCP server). This is faster and simpler than proxying MCP.

**Write endpoints**:

| Method | Path | Maps to | Parameters |
|--------|------|---------|------------|
| `POST` | `/api/drawers` | `mempalace_add_drawer` | `wing`, `room`, `content`, `metadata` (incl. `source_url`, `title`, `hall`, `timestamp`) |
| `PUT` | `/api/drawers/{id}` | `mempalace_update_drawer` | `content?`, `metadata?` |
| `DELETE` | `/api/drawers/{id}` | `mempalace_delete_drawer` | — |

**Read endpoints**:

| Method | Path | Maps to | Parameters |
|--------|------|---------|------------|
| `GET` | `/api/search` | `mempalace_search` | `query`, `wing?`, `room?`, `max_distance?`, `limit?` |
| `GET` | `/api/status` | `mempalace_status` | — |
| `GET` | `/api/taxonomy` | `mempalace_get_taxonomy` | — |
| `GET` | `/api/wings` | `mempalace_list_wings` | — |
| `GET` | `/api/rooms` | `mempalace_list_rooms` | `wing?` |
| `GET` | `/api/drawers/by-url` | custom (search metadata) | `url` |

**Duplicate detection endpoint**:

| Method | Path | Purpose | Parameters |
|--------|------|---------|------------|
| `GET` | `/api/drawers/by-url` | Check if URL already captured | `url` (exact match on `source_url` in metadata) |

Returns `{ "exists": true, "drawer_id": "...", "title": "..." }` or `{ "exists": false }`.

**CORS**: Allow `chrome-extension://*` and `moz-extension://*` origins.

**Health check**: `GET /api/health` → `{ "status": "ok", "drawers": 1234 }`.

### 3.2 Browser Extension

**Target browsers**: Chrome (Manifest V3) + Firefox (WebExtension API, MV3 where supported, MV2 fallback if needed).

**File structure**:
```
memorymark-extension/
  manifest.json
  background.js          # Service worker — shortcut handling, HTTP bridge calls
  content.js             # DOM extraction + overlay injection
  content.css            # Overlay styles
  offscreen.html         # Gemma WASM host (Chrome offscreen document)
  offscreen.js           # MediaPipe + Gemma initialization
  lib/
    turndown.min.js      # HTML → Markdown conversion
    readability.min.js   # Article content extraction
    turndown-plugin-gfm.min.js  # Table support
  icons/
    icon-16.png
    icon-48.png
    icon-128.png
```

**Bundle size** (excluding Gemma model): ~100KB gzipped.

**Permissions**:
```json
{
  "permissions": ["activeTab", "storage", "offscreen", "commands"],
  "host_permissions": ["http://localhost:21517/*"]
}
```

**Keyboard shortcut**:
```json
{
  "commands": {
    "capture-page": {
      "suggested_key": { "default": "Ctrl+Shift+M", "mac": "Command+Shift+M" },
      "description": "Capture current page to MemPalace"
    }
  }
}
```

### 3.3 Content Script — DOM Extraction

**Libraries**:
- **@mozilla/readability** (~65KB): Extracts main article content, strips nav/ads/sidebars
- **turndown.js** (~19KB): Converts cleaned HTML to Markdown
- **turndown-plugin-gfm** (~5KB): Adds GFM table and strikethrough support

**Extraction pipeline**:
```
1. Clone document           document.cloneNode(true)
2. Extract article          new Readability(clone).parse()
3. Convert to Markdown      turndownService.turndown(article.content)
4. Extract metadata         title, author, date, description from meta tags
5. Compose payload          { content: markdown, title, source_url, timestamp }
6. Send to background       chrome.runtime.sendMessage()
```

**Metadata extraction** (no library, direct DOM queries):
```javascript
{
  title: document.querySelector('meta[property="og:title"]')?.content
         || document.title,
  author: document.querySelector('meta[name="author"]')?.content
          || document.querySelector('meta[property="article:author"]')?.content,
  date: document.querySelector('meta[property="article:published_time"]')?.content,
  description: document.querySelector('meta[name="description"]')?.content
               || document.querySelector('meta[property="og:description"]')?.content,
  source_url: window.location.href
}
```

**Turndown configuration**:
```javascript
const turndownService = new TurndownService({
  headingStyle: 'atx',        // # headings
  codeBlockStyle: 'fenced',   // ```code blocks```
  bulletListMarker: '-',
});
turndownService.use(gfm);      // tables, strikethrough
```

**Content format sent to MemPalace**:
```markdown
# {title}

> Source: {source_url}
> Author: {author} | Date: {date}

{full markdown body from Readability + Turndown}
```

### 3.4 Overlay UI

The overlay is injected into the page DOM by the content script. It has two modes: **capture feedback** and **chat**.

#### 3.4.1 Capture Feedback Mode

Shown immediately when the shortcut is pressed. Minimal, non-intrusive.

```
┌──────────────────────────────────┐
│  MemoryMark                   ✕  │
│                                  │
│  ✓  Page saved to MemPalace      │
│     "Grok 4 Release Notes"       │
│     Wing: Web                    │
│                                  │
│  [Open Chat]                     │
└──────────────────────────────────┘
```

**States**:
- **Saving**: Spinner + "Saving page..."
- **Saved**: Checkmark + title + wing
- **Already exists**: Info icon + "Already in MemPalace" + link to chat about it
- **Error**: Red icon + error message + retry button
- **Bridge unreachable**: Warning + "MemPalace bridge not running. Start with `docker compose up -d`"

**Position**: Fixed, top-right corner of viewport. Auto-dismisses after 4 seconds unless hovered or chat is opened.

#### 3.4.2 Chat Mode

Opened via "Open Chat" button in capture feedback, or by pressing the shortcut when the overlay is already showing.

```
┌──────────────────────────────────┐
│  MemoryMark Chat              ✕  │
│──────────────────────────────────│
│                                  │
│  🤖 How can I help with your     │
│     saved pages?                 │
│                                  │
│  You: /summarize                 │
│                                  │
│  🤖 This article covers the      │
│     release of Grok 4...         │
│                                  │
│──────────────────────────────────│
│  [Type a message... ]     [Send] │
└──────────────────────────────────┘
```

**Size**: ~400px wide, ~500px tall, resizable. Positioned as a slide-in panel on the right edge.

**Chat commands**:
- `/summarize` — Summarize the current page's stored content (Gemma generates summary from the stored markdown)
- `/search <query>` — Search across all stored pages
- `/status` — Show MemPalace stats (drawer count, wings)
- Free-form questions — Gemma uses MemPalace search tools to answer

### 3.5 Gemma WASM — In-Browser LLM

**Model**: Gemma 4 E2B (quantized, ~2GB `.task` file).

**Runtime**: MediaPipe Tasks GenAI + WebGPU.

**Hosting in extension**: Chrome's **offscreen document** API hosts the WebGPU context and WASM runtime. The offscreen document is created on extension install and persists while needed. Firefox alternative: a hidden background page or extension tab.

**Model caching**:
- On extension install, the background worker triggers the model download
- Model is cached in **Origin Private File System (OPFS)** for persistence across sessions
- Download progress is shown via extension badge (e.g., "47%" on the icon)
- If WebGPU/shader-f16 is unavailable, skip download and disable chat (capture still works)

**Gemma initialization flow**:
```
Extension Install
  → Check WebGPU + shader-f16 support
  → If supported: stream-download model to OPFS (~2GB)
  → Badge shows progress: "DL 47%"
  → On complete: badge shows "✓"
  → Model ready for chat

First Chat Open
  → Create offscreen document (if not exists)
  → Load model from OPFS into MediaPipe
  → Initialize ChatMediaPipeGenAI
  → Ready for inference
```

**Gemma tools** (registered via LangChain tool binding, call FastAPI bridge):

| Tool | Description | Bridge Endpoint |
|------|-------------|-----------------|
| `search_memory` | Search MemPalace for relevant documents | `GET /api/search` |
| `get_page_content` | Retrieve the stored content for a specific URL | `GET /api/drawers/by-url` |
| `list_recent` | List recently captured pages | `GET /api/search` (time-sorted) |

**System prompt for Gemma**:
```
You are a memory assistant running locally in the browser.
You help the user recall and understand web pages they have saved.
Use the search_memory tool to find relevant saved pages before answering.
Use get_page_content to retrieve full page content when needed.
Keep answers concise. Cite sources by title and URL.
When the user says /summarize, summarize the current page's stored content.
```

---

## 4. Data Flows

### 4.1 Capture Flow (Shortcut → MemPalace)

```
1. User presses Ctrl+Shift+M (or Cmd+Shift+M on Mac)

2. Background service worker receives command event
   → Sends "extract" message to content script

3. Content script:
   a. Clones DOM
   b. Runs Readability → extracts article
   c. Runs Turndown → converts to Markdown
   d. Extracts metadata (title, URL, author, date)
   e. Returns payload to background worker

4. Background worker:
   a. Calls GET /api/drawers/by-url?url={source_url}
   b. If exists → sends "already-exists" to content script
   c. If not → calls POST /api/drawers with:
      {
        "wing": "Web",
        "room": "Captures",
        "content": "# Title\n\n> Source: ...\n\n{markdown body}",
        "metadata": {
          "source_url": "https://...",
          "title": "Page Title",
          "hall": "hall_discoveries",
          "timestamp": "2026-04-13T09:00:00Z"
        }
      }
   d. Sends result status to content script

5. Content script:
   → Shows overlay with appropriate state (saved / already exists / error)
   → Auto-dismiss after 4 seconds
```

### 4.2 Chat Flow (Overlay → Gemma → MemPalace)

```
1. User opens chat (via overlay button or second shortcut press)

2. Content script shows chat panel

3. User types message (or /summarize command)

4. Message sent to background worker → relayed to offscreen document

5. Offscreen document:
   a. Gemma receives message with tool declarations
   b. Gemma decides to call search_memory or get_page_content
   c. Tool calls are executed against FastAPI bridge
   d. Gemma generates response using tool results
   e. Response streamed back via message passing

6. Content script:
   → Renders streaming response in chat panel
   → Tool call indicators shown inline (e.g., "Searching memory...")
```

### 4.3 /summarize Command Flow

```
1. User types "/summarize" in chat

2. Background worker:
   a. Gets current tab URL
   b. Calls GET /api/drawers/by-url?url={current_url}
   c. If found: passes stored content to Gemma with prompt
      "Summarize this document concisely: {content}"
   d. If not found: "This page hasn't been captured yet. Press Ctrl+Shift+M to save it first."

3. Gemma generates summary → streamed to chat overlay
```

---

## 5. Defaults and Configuration

### 5.1 MemPalace Defaults (v1)

| Field | Default Value | Notes |
|-------|--------------|-------|
| `wing` | `"Web"` | Always "Web" in v1. Configurable wing selection is a future requirement. |
| `room` | `"Captures"` | Single room for all web captures in v1. |
| `hall` | `"hall_discoveries"` | Default hall for web captures. |
| `timestamp` | Current ISO 8601 | Auto-generated at capture time. |

### 5.2 Extension Settings (stored in `chrome.storage.local`)

| Setting | Default | Description |
|---------|---------|-------------|
| `bridgeUrl` | `http://localhost:21517` | MemPalace bridge URL |
| `autoClose` | `4000` | Overlay auto-dismiss time (ms) |
| `gemmaEnabled` | `true` | Whether to download/enable Gemma |

---

## 6. Docker Compose

```yaml
# docker-compose.yml
services:
  memorymark-bridge:
    build: ./bridge
    ports:
      - "127.0.0.1:21517:21517"
    volumes:
      - mempalace-data:/root/.mempalace/palace
    environment:
      - MEMORYMARK_PORT=21517
    restart: unless-stopped

volumes:
  mempalace-data:
```

```dockerfile
# bridge/Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install mempalace fastapi uvicorn
COPY server.py .
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "21517"]
```

---

## 7. Cross-Browser Compatibility

| Feature | Chrome | Firefox |
|---------|--------|---------|
| Manifest | V3 | V3 (Firefox 109+) |
| Offscreen document (Gemma host) | `chrome.offscreen` API | Not available — use persistent background page or extension tab |
| WebGPU | Stable | Behind flag (`dom.webgpu.enabled`) |
| Keyboard shortcuts | `chrome.commands` | `browser.commands` |
| Storage | `chrome.storage` | `browser.storage` |
| Native messaging | `chrome.runtime.connectNative` | `browser.runtime.connectNative` |

**Abstraction layer**: Use `globalThis.browser || globalThis.chrome` for API compatibility. For the offscreen document gap in Firefox, fall back to a hidden extension page (`about:blank` with extension origin).

**Gemma in Firefox**: WebGPU must be enabled. If unavailable, chat is disabled gracefully — capture still works.

---

## 8. Error Handling

| Scenario | Behavior |
|----------|----------|
| Bridge unreachable | Overlay shows warning: "MemPalace bridge not running" with Docker start instructions |
| Readability fails (no article extracted) | Fall back to `document.body.innerHTML` through Turndown (capture full page) |
| Gemma model download fails | Retry with exponential backoff. Badge shows error state. Chat disabled until resolved. |
| WebGPU not available | Skip Gemma entirely. Capture works. Chat shows "Gemma requires WebGPU" message. |
| MemPalace write fails | Overlay shows error + retry button. Content is not lost (held in memory until retry succeeds or user dismisses). |
| Content too large (>100K chars) | Truncate markdown to MemPalace's 100,000 char limit. Show warning in overlay. |

---

## 9. Future Requirements (Not in v1)

- **Configurable wing selection**: Overlay dropdown to pick/create wings before saving
- **Custom room mapping**: Rules to auto-assign room based on URL patterns (e.g., `github.com/*` → room "Code")
- **Gemma auto-categorization**: Use Gemma to suggest wing/room based on page content
- **Bulk capture**: Right-click context menu to capture linked pages
- **Capture history**: Extension popup showing recently captured pages
- **Knowledge graph integration**: Extract entities and relationships from captured pages via `mempalace_kg_add`

---

## 10. File Map — Key Files to Create

```
memorymark/
  docker-compose.yml                    # Docker orchestration
  bridge/
    Dockerfile                          # Python + MemPalace + FastAPI
    server.py                           # FastAPI HTTP bridge
    requirements.txt                    # mempalace, fastapi, uvicorn
  extension/
    manifest.json                       # MV3 manifest (Chrome primary)
    manifest.firefox.json               # Firefox overrides (if needed)
    background.js                       # Service worker
    content.js                          # DOM extraction + overlay
    content.css                         # Overlay styles
    offscreen.html                      # Gemma host document
    offscreen.js                        # MediaPipe + Gemma runtime
    lib/
      turndown.min.js
      readability.min.js
      turndown-plugin-gfm.min.js
    icons/
      icon-16.png
      icon-48.png
      icon-128.png
  Makefile                              # Build extension zip, docker up/down
```

---

## 11. Implementation Order

1. **Docker bridge** — `bridge/server.py` with FastAPI wrapping MemPalace. Dockerfile + docker-compose. Test with curl.
2. **Extension skeleton** — manifest.json, background.js (shortcut listener), content.js (empty overlay injection). Verify shortcut works.
3. **DOM extraction** — Integrate Readability + Turndown in content.js. Verify Markdown output quality on 5+ diverse pages.
4. **Capture flow** — Wire background.js → bridge HTTP calls. Implement overlay feedback states (saving/saved/exists/error).
5. **Duplicate detection** — `GET /api/drawers/by-url` endpoint + visual feedback in overlay.
6. **Gemma integration** — Offscreen document, model download + OPFS caching, badge progress.
7. **Chat overlay** — Chat UI in content.js, message routing to offscreen Gemma, streaming responses.
8. **Gemma tools** — `search_memory`, `get_page_content`, `/summarize` command.
9. **Firefox compatibility** — Test and adapt (background page fallback, WebGPU check).
10. **Makefile + packaging** — Build zips for Chrome and Firefox.

---

## 12. Verification

**Bridge**:
```bash
docker compose up -d
curl http://localhost:21517/api/health
curl -X POST http://localhost:21517/api/drawers \
  -H 'Content-Type: application/json' \
  -d '{"wing":"Web","room":"Captures","content":"# Test\nHello","metadata":{"source_url":"https://example.com","title":"Test"}}'
curl 'http://localhost:21517/api/drawers/by-url?url=https://example.com'
curl 'http://localhost:21517/api/search?query=test&limit=5'
```

**Extension capture**:
1. Load unpacked extension in Chrome
2. Navigate to any article page
3. Press Ctrl+Shift+M → overlay shows "Saving..." then checkmark
4. Press Ctrl+Shift+M again → overlay shows "Already in MemPalace"
5. Verify in bridge: `curl 'http://localhost:21517/api/search?query=<page title>'`

**Gemma chat**:
1. Wait for model download (badge shows progress)
2. Open chat via overlay
3. Type `/summarize` on a captured page → get summary
4. Type "what pages have I saved about X?" → Gemma searches and answers
5. Verify tool calls hit the bridge (check Docker logs)

**Firefox**:
1. Load extension in Firefox (about:debugging)
2. Enable WebGPU flag if needed
3. Repeat capture and chat tests
