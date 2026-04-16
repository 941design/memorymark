# MemoryMark Bridge REST API

HTTP endpoints for browser extensions and other REST clients to read and write
palace drawers. Prefer these endpoints over MCP when the client has structured
metadata (source URL, title, timestamp) — the REST path preserves richer
metadata in both Chroma storage and SSE event payloads.

## Base URL

```
http://localhost:21517/api
```

| Property       | Value                                          |
|----------------|------------------------------------------------|
| Authentication | None (loopback-only)                           |
| Content-Type   | `application/json` (request and response)      |
| CORS           | `chrome-extension://` and `moz-extension://` origins only |

---

## Health

### `GET /api/health`

Quick liveness check. Returns drawer count.

**Response:**
```json
{ "status": "ok", "drawers": 42 }
```

---

## Write endpoints

### `POST /api/drawers`

Create a new drawer. Content-addressed — posting identical content to the same
wing/room is idempotent (returns success without a duplicate WAL entry or SSE
event).

**Request body:**

| Field              | Type   | Required | Description                               |
|--------------------|--------|----------|-------------------------------------------|
| `wing`             | string | **yes**  | Wing name                                 |
| `room`             | string | **yes**  | Room name                                 |
| `content`          | string | **yes**  | Full content to store                     |
| `metadata`         | object | no       | Optional metadata object (see below)      |

**Metadata object:**

| Field        | Type   | Description                                     |
|--------------|--------|-------------------------------------------------|
| `source_url` | string | URL the content was captured from               |
| `title`      | string | Page or document title                          |
| `timestamp`  | string | Capture timestamp (ISO-8601)                    |

**Response (201):**
```json
{ "success": true, "drawer_id": "drawer_Web_Captures_a1b2c3..." }
```

**SSE event emitted:** `add_drawer` with `source_url`, `content_hash`, `added_by`, `content_length`, `content_preview`.

---

### `PUT /api/drawers/{drawer_id}`

Update an existing drawer's content, location, and/or metadata. All fields are
optional — omit a field to keep its current value.

**Request body:**

| Field      | Type   | Required | Description                          |
|------------|--------|----------|--------------------------------------|
| `content`  | string | no       | New content                          |
| `wing`     | string | no       | New wing                             |
| `room`     | string | no       | New room                             |
| `metadata` | object | no       | Metadata updates (same shape as create) |

**Response (200):**
```json
{ "success": true, "drawer_id": "drawer_Web_Captures_a1b2c3..." }
```

**SSE event emitted:** `update_drawer` with `added_by`, `source_url`, `content_hash`, plus location diff fields.

---

### `DELETE /api/drawers/{drawer_id}`

Delete a drawer by ID. Irreversible.

**Response (200):**
```json
{ "success": true }
```

**Errors:** 404 if not found.

---

## Read endpoints

### `GET /api/drawers/{drawer_id}`

Fetch a single drawer by ID.

**Response:**
```json
{
  "drawer_id": "drawer_Web_Captures_a1b2c3...",
  "content": "...",
  "wing": "Web",
  "room": "Captures",
  "title": "Page Title",
  "source_url": "https://example.com/page"
}
```

---

### `GET /api/drawers/by-url`

Look up a drawer by its source URL. Useful for checking whether a page has
already been captured.

| Parameter         | Type    | Required | Description                     |
|-------------------|---------|----------|---------------------------------|
| `url`             | string  | **yes**  | Source URL to look up           |
| `include_content` | boolean | no       | Include full content (default `false`) |

**Response (found):**
```json
{
  "exists": true,
  "drawer_id": "drawer_Web_Captures_a1b2c3...",
  "title": "Page Title"
}
```

**Response (not found):**
```json
{ "exists": false }
```

---

### `GET /api/search`

Semantic search across palace drawers.

| Parameter      | Type    | Required | Description                                |
|----------------|---------|----------|--------------------------------------------|
| `query`        | string  | **yes**  | Search keywords or question                |
| `wing`         | string  | no       | Filter by wing                             |
| `room`         | string  | no       | Filter by room                             |
| `max_distance` | number  | no       | Max cosine distance (default 1.5)          |
| `limit`        | integer | no       | Max results (default 5)                    |

---

### `GET /api/status`

Palace overview: total drawers, wing and room counts.

**Response:**
```json
{
  "total_drawers": 42,
  "wings": { "Web": 12, "Code": 30 },
  "rooms": { "Captures": 8, "Architecture": 4 }
}
```

---

### `GET /api/taxonomy`

Full wing -> room -> drawer count tree.

**Response:**
```json
{
  "taxonomy": {
    "Web": { "Captures": 8, "Articles": 4 },
    "Code": { "Architecture": 12 }
  }
}
```

---

### `GET /api/wings`

List all wings with drawer counts.

---

### `GET /api/rooms`

List rooms, optionally filtered by wing.

| Parameter | Type   | Required | Description            |
|-----------|--------|----------|------------------------|
| `wing`    | string | no       | Filter by wing         |

---

## SSE event stream

### `GET /api/events`

Real-time Server-Sent Events stream of palace mutations. See
[sse-events.md](sse-events.md) for full documentation.

---

## Knowledge Graph

### `GET /api/kg/query`

Query the knowledge graph for an entity's relationships. Returns typed facts
with temporal validity.

| Parameter   | Type   | Required | Description                                        |
|-------------|--------|----------|----------------------------------------------------|
| `entity`    | string | **yes**  | Entity to query (e.g. `Alice`, `MyProject`)        |
| `as_of`     | string | no       | Only facts valid at this date (`YYYY-MM-DD`)       |
| `direction` | string | no       | `outgoing`, `incoming`, or `both` (default `both`) |

**Response:**
```json
{
  "entity": "Alice",
  "as_of": null,
  "facts": [
    {
      "direction": "outgoing",
      "subject": "Alice",
      "predicate": "works_at",
      "object": "Acme",
      "valid_from": "2025-01-01",
      "valid_to": null,
      "confidence": 1.0,
      "source_closet": null,
      "current": true
    }
  ],
  "count": 1
}
```

---

### `POST /api/kg/facts`

Add a fact triple to the knowledge graph.

**Request body:**

| Field           | Type   | Required | Description                                  |
|-----------------|--------|----------|----------------------------------------------|
| `subject`       | string | **yes**  | The entity doing/being something             |
| `predicate`     | string | **yes**  | Relationship type (e.g. `works_at`, `loves`) |
| `object`        | string | **yes**  | The connected entity                         |
| `valid_from`    | string | no       | When this became true (`YYYY-MM-DD`)         |
| `source_closet` | string | no       | Drawer ID where this fact appears            |

**Response (201):**
```json
{
  "success": true,
  "triple_id": "t_alice_works_at_acme_a810f2b1de40",
  "fact": "Alice \u2192 works_at \u2192 Acme"
}
```

---

### `POST /api/kg/invalidate`

Mark a fact as no longer true (e.g. job ended, moved house).

**Request body:**

| Field       | Type   | Required | Description                                     |
|-------------|--------|----------|-------------------------------------------------|
| `subject`   | string | **yes**  | Entity                                          |
| `predicate` | string | **yes**  | Relationship                                    |
| `object`    | string | **yes**  | Connected entity                                |
| `ended`     | string | no       | When it stopped being true (`YYYY-MM-DD`, default: today) |

**Response (200):**
```json
{
  "success": true,
  "fact": "Alice \u2192 works_at \u2192 Acme",
  "ended": "2026-04-16"
}
```

---

## REST vs MCP: when to use which

| Concern              | REST (`/api/*`)             | MCP (`/mcp`)               |
|----------------------|-----------------------------|-----------------------------|
| Rich metadata        | `source_url`, `title`, `timestamp` | `source_file` only   |
| SSE event payload    | Includes `source_url`, `content_hash`, `added_by` on all ops | Missing `source_url` and `content_hash` |
| URL lookup           | `GET /api/drawers/by-url`   | Not available               |
| Idempotency          | Content-addressed, no duplicate SSE | Content-addressed   |
| Agent harness access | Manual HTTP calls           | Native MCP tool discovery   |
| Intended client      | Browser extensions, scripts | AI agent harnesses          |

**Recommendation:** Use REST when the client has a source URL or structured
metadata (browser extensions, web scrapers, CLI scripts). Use MCP when the
client is an AI agent harness that benefits from tool discovery.

---

## Recommended content format: HTML-to-Markdown conversion

Drawer content stored via `POST /api/drawers` should be Markdown. The
MemoryMark browser extension converts web pages to Markdown before posting
using the pipeline described below. Third-party clients (scrapers, CLI tools,
MCP agents) should follow the same approach for consistent search quality and
readability.

### Pipeline overview

```
HTML page
  ├─ 1. Extract metadata from <meta> tags
  ├─ 2. Readability: strip nav/ads/boilerplate, isolate article body
  ├─ 3. Turndown: convert cleaned HTML → Markdown (GFM)
  ├─ 4. Fallback: full <body> conversion if Readability fails
  ├─ 5. Truncate to 100 000 characters
  └─ 6. Prepend header block (title, source URL, author/date)
```

### Step 1 — Metadata extraction

Read standard HTML meta tags in priority order:

| Field        | Primary source                         | Fallback                             |
|--------------|----------------------------------------|--------------------------------------|
| `title`      | `<meta property="og:title">`           | `document.title`                     |
| `author`     | `<meta name="author">`                 | `<meta property="article:author">`   |
| `date`       | `<meta property="article:published_time">` | —                                |
| `description`| `<meta name="description">`            | `<meta property="og:description">`   |
| `source_url` | `window.location.href`                 | —                                    |

### Step 2 — Article extraction (Readability)

Clone the full DOM and pass it to Mozilla's
[Readability](https://github.com/mozilla/readability) algorithm. Readability
scores DOM nodes by content density, removing navigation, sidebars, ads,
footers, and other boilerplate. The result is a clean HTML fragment containing
only the article body.

If Readability returns a better `title` or `byline` than the meta tags, prefer
those values.

### Step 3 — HTML-to-Markdown conversion (Turndown + GFM)

Convert the Readability HTML to Markdown using
[Turndown](https://github.com/mixmark-io/turndown) with the GFM plugin:

| Setting            | Value    | Effect                                |
|--------------------|----------|---------------------------------------|
| `headingStyle`     | `atx`    | `<h1>` → `# `, `<h2>` → `## `, etc. |
| `codeBlockStyle`   | `fenced` | `<pre><code>` → ` ``` `              |
| `bulletListMarker` | `-`      | `<li>` in `<ul>` → `- `              |
| GFM plugin         | enabled  | Tables, strikethrough, task lists     |

Element mapping summary:

| HTML                         | Markdown                    |
|------------------------------|-----------------------------|
| `<h1>`–`<h6>`               | `#` … `######`              |
| `<strong>`, `<b>`           | `**bold**`                  |
| `<em>`, `<i>`               | `*italic*`                  |
| `<code>`                     | `` `inline` ``              |
| `<pre><code class="lang-x">` | ` ```x `                   |
| `<a href="u">text</a>`      | `[text](u)`                 |
| `<img src="u" alt="d">`     | `![d](u)`                   |
| `<blockquote>`               | `> `                        |
| `<ul>` / `<ol>`             | `- ` / `1. ` (nested OK)   |
| `<table>` (GFM)             | `\| col \| col \|` pipe tables |
| `<del>`                      | `~~strikethrough~~`         |
| `<input type="checkbox">`   | `- [x]` / `- [ ]`          |

### Step 4 — Fallback

If Readability fails (e.g. non-article page), convert `document.body.innerHTML`
directly through Turndown. If Turndown also fails, fall back to plain
`document.body.innerText`.

### Step 5 — Truncation

Cap the Markdown body at **100 000 characters**. Flag `truncated: true` in the
return value so callers can note the loss.

### Step 6 — Header block

Prepend a standard header so every drawer is self-describing:

```markdown
# Page Title

> Source: https://example.com/article
> Author: Jane Doe | Date: 2026-04-16

[Markdown body from steps 2–5]
```

The `> Author: … | Date: …` line is omitted when neither field is available.

### Recommendations for third-party clients

1. **Use the same libraries** — Readability and Turndown (or equivalents like
   `html2text` in Python with `body_width=0`) to keep Markdown style consistent
   across sources.
2. **Always include the header block** — the `# Title` heading and `> Source:`
   blockquote give the vector search meaningful anchor text and enable
   provenance tracking.
3. **Pass `source_url` in metadata** — even though the URL is embedded in the
   Markdown header, the structured `metadata.source_url` field powers
   `GET /api/drawers/by-url` deduplication.
4. **Respect the 100k limit** — very large pages degrade embedding quality;
   split into multiple drawers if needed.
