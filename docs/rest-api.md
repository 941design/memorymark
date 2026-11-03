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
| `added_by`         | string | no       | Creator tag; persisted verbatim and echoed in SSE events. Defaults to `"memorymark"`. Agents such as daydreamer must set this to mark their own writes. |
| `metadata`         | object | no       | Optional metadata object (see below)      |

**Metadata object:**

| Field        | Type   | Description                                     |
|--------------|--------|-------------------------------------------------|
| `source_url` | string | URL the content was captured from               |
| `title`      | string | Page or document title                          |
| `timestamp`  | string | Capture timestamp (ISO-8601)                    |

**Response (201):**
```json
{ "success": true, "drawer_id": "drawer_Web_Captures_a1b2c3...", "indexed": true }
```

`indexed` reports whether the drawer's passages were written to the passage
index. `false` never means the drawer was lost — it is stored; the index is
repaired by the next reconcile (bridge start-up or `make reindex`). The same
field is returned by `PUT`, `DELETE` and `POST /api/diary/write`.

Long content is indexed as passages for search; the drawer itself is stored
verbatim as one drawer, and `drawer_id` is the id to read, update or delete it.

**SSE event emitted:** `add_drawer` with `drawer_id`, `wing`, `room`,
`added_by`, `content_length`, `content_preview`, `source_url`, `content_hash`.

---

### `PUT /api/drawers/{drawer_id}`

Update an existing drawer's content, location, and/or metadata. All fields are
optional — omit a field to keep its current value.

Creator provenance is preserved: `added_by` is stored at create time and cannot
be changed by an update. The value emitted on the `update_drawer` SSE event
(and written to the WAL) reflects the stored creator, not the identity of the
agent performing the update.

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

**SSE event emitted:** `update_drawer` with `drawer_id`, `old_wing`, `old_room`,
`new_wing`, `new_room`, `content_changed`, `content_preview`, `added_by`
(stored creator), `source_url`, `content_hash`.

---

### `DELETE /api/drawers/{drawer_id}`

Delete a drawer by ID. Irreversible.

**Response (200):**
```json
{ "success": true, "indexed": true }
```

Also removes the drawer's passages from the passage index.

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
  "added_by": "memorymark",
  "content_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "metadata": {
    "source_url": "https://example.com/page",
    "title": "Page Title",
    "timestamp": "2026-04-13T00:00:00Z"
  }
}
```

| Field          | Type   | Description                                               |
|----------------|--------|-----------------------------------------------------------|
| `drawer_id`    | string | Content-addressed drawer ID                               |
| `content`      | string | Full drawer content (Markdown)                            |
| `wing`         | string | Wing the drawer is filed in                               |
| `room`         | string | Room the drawer is filed in                               |
| `added_by`     | string | Original creator (`memorymark`, `mcp`, or an agent name)  |
| `content_hash` | string | SHA-256 hex digest of the current content                 |
| `metadata`     | object | Normalized metadata with `source_url`, `title`, `timestamp` |

**Errors:** 404 if the drawer does not exist.

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

Semantic search across palace drawers, **one hit per drawer**. Every drawer is
indexed as token-bounded passages (see "Passage index" below); a drawer is
ranked by its best-matching passage, so text deep inside a long article is
found, not only its opening.

| Parameter         | Type    | Required | Description                                |
|-------------------|---------|----------|--------------------------------------------|
| `query`           | string  | **yes**  | Search keywords or question                |
| `wing`            | string  | no       | Filter by wing                             |
| `room`            | string  | no       | Filter by room                             |
| `max_distance`    | number  | no       | Max cosine distance of the best passage (unbounded when omitted) |
| `limit`           | integer | no       | Max results (default 5, capped at 50)      |
| `include_content` | boolean | no       | Include each drawer's full `content` (default `true`). Set `false` when the passage is enough — full articles can be up to 200 000 characters. |

**Response:**
```json
{
  "query": "python programming",
  "filters": { "wing": null, "room": null },
  "index": "passages",
  "results": [
    {
      "drawer_id": "drawer_Web_Captures_a1b2c3...",
      "content": "# Python Tutorial\n...",
      "passage": "...the best-matching passage...",
      "passage_start": 4210,
      "passage_end": 5188,
      "matched_passages": 3,
      "metadata": {
        "source_url": "https://example.com/python",
        "title": "Python Tutorial",
        "timestamp": "2026-04-13T01:00:00Z"
      },
      "distance": 0.3421,
      "wing": "Web",
      "room": "Captures",
      "added_by": "memorymark"
    }
  ]
}
```

Each result object is guaranteed to include `drawer_id`, `passage`,
`passage_start`, `passage_end` (char offsets into the drawer content, so
`content[passage_start:passage_end] == passage`), `matched_passages` (how many
of the drawer's passages were among the nearest neighbours), `metadata` (with
`source_url`, `title`, `timestamp`), `distance`, `wing`, `room`, `added_by`,
and `content` unless `include_content=false`.

`distance` is the best *passage's* distance. Passages match more tightly than
whole articles did, so distances are generally lower than before the passage
index existed; re-tune any `max_distance` thresholds accordingly.

`index` is `"passages"` normally, or `"articles"` while the passage index is
still empty (fresh install before the first reconcile): then results come from
the legacy one-vector-per-drawer search, and `passage` is the drawer's opening.

---

### `GET /api/status`

Palace overview: total drawers, wing and room counts.

**Response:**
```json
{
  "total_drawers": 42,
  "wings": { "Web": 12, "Code": 30 },
  "rooms": { "Captures": 8, "Architecture": 4 },
  "passage_index": {
    "path": "/…/palace/memorymark-passages",
    "passages": 391,
    "indexed_articles": 42,
    "coverage": 1.0,
    "chunker_version": 1,
    "reconcile": { "state": "done", "mode": "quick", "total": 0, "processed": 0, "...": "..." }
  }
}
```

Counts are drawers (articles), never passages. `passage_index.coverage` is
`indexed_articles / total_drawers`; `reconcile` shows the progress of the
background reconcile started at bridge start-up.

### Passage index

MemPalace's embedding model reads only the first 256 tokens of a drawer. The
bridge therefore maintains a derived passage index (`<palace>/memorymark-passages/`,
override with `MEMORYMARK_INDEX_PATH`): each drawer is split into overlapping
passages of at most 230 model tokens, preferring paragraph, line and sentence
boundaries, and each passage is embedded separately. Passages store only char
offsets into their drawer, never a copy of the text.

- The article store is never modified by the index. Deleting the index
  directory is a complete rollback (search falls back to article level).
- REST and MCP writes update the index immediately; the bridge reconciles it
  in the background at start-up (`MEMORYMARK_RECONCILE_ON_START=0` disables).
  Writers that bypass the bridge are picked up by that reconcile (id/wing/room
  changes) or by `make reindex` (also content edits, via content hashes).
- `make reindex` backs up the palace, rebuilds what is missing or stale, and
  verifies that every article is still present and byte-identical and fully
  covered by passages. `make reindex-verify` only verifies.

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

Supported query parameters (filters and replay):

| Parameter         | Description                                               |
|-------------------|-----------------------------------------------------------|
| `ops`             | Comma-separated operation names (e.g. `add_drawer,update_drawer`). Server-side filtering; unmatched events are dropped in both replay and live phases. |
| `since_timestamp` | ISO-8601 timestamp. Replay all events with `timestamp >= value`, then stream live. Required fallback when a `Last-Event-ID` cursor is older than the ring buffer and a `cursor-stale` frame has been emitted. |

The `Last-Event-ID` request header (set automatically by `EventSource`) takes
priority over `since_timestamp` when both are present.

---

## Diary

### `POST /api/diary/write`

Write an agent diary entry. The daydreamer and other autonomous agents MUST
use this endpoint directly (no MCP fallback) so every diary write is captured
in the bridge's WAL and appears in the SSE stream.

**Request body:**

| Field        | Type   | Required | Description                                    |
|--------------|--------|----------|------------------------------------------------|
| `agent_name` | string | **yes**  | Agent identity; each agent gets its own wing   |
| `entry`      | string | **yes**  | Diary entry body (AAAK format recommended)     |
| `topic`      | string | no       | Logical topic bucket (default `"general"`)     |

**Response (201):**
```json
{
  "success": true,
  "entry_id": "diary_wing_daydreamer_20260416_101530_abc123def456",
  "agent": "daydreamer",
  "topic": "synthesis",
  "timestamp": "2026-04-16T10:15:30.123456"
}
```

Diary entries are drawers too and are indexed like any other drawer.

**SSE event emitted:** `diary_write` with `agent_name`, `topic`, `entry_id`,
`entry_preview`.

---

## Knowledge Graph

### Identifier validation

The `entity` parameter on `GET /api/kg/query` and the `subject`, `predicate`,
and `object` fields on `POST /api/kg/facts` and `POST /api/kg/invalidate`
are validated identically before the palace writes or reads a triple.

**Rule:**

```
^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$
```

- **Allowed characters:** Unicode alphanumerics (`\w` minus `_`), underscore,
  space (` `), dot (`.`), apostrophe (`'`), and hyphen (`-`).
- **Boundary constraint:** the first and last character must be alphanumeric
  (not `_`, space, `.`, `'`, or `-`).
- **Max length:** 128 characters (checked before the regex; length failures
  return a distinct error).
- **Explicitly rejected:** `:`, `/`, `\`, `#`, `%`, `,`, `+`, `?`, `@`, `=`,
  `&`, `"`, `;`, `..`, null bytes, and any other punctuation not in the
  allowed set.

Clients that need to namespace triples (e.g. separate opinion-anchored facts
from entity-anchored facts) should encode the namespace with an allowed
separator such as `_` or `-` rather than `:`. For example, use
`opinion_<id>` instead of `opinion:<id>`.

**Error response (400):**

```json
{
  "detail": "subject contains invalid characters",
  "field": "subject",
  "value": "opinion:abc_123",
  "allowed_pattern": "^(?:[^\\W_]|[^\\W_][\\w .'-]{0,126}[^\\W_])$",
  "allowed_description": "Unicode alphanumerics, underscore, space, dot, apostrophe, and hyphen. First and last character must be alphanumeric. Max 128 characters.",
  "invalid_chars": [":"]
}
```

Fields on the error body:

| Field                 | Present when                                                  |
|-----------------------|---------------------------------------------------------------|
| `detail`              | Always — the terse message from the palace.                   |
| `field`               | The request field that failed (`subject`, `predicate`, `object`, `entity`). |
| `value`               | The exact value the caller submitted, echoed back.            |
| `allowed_pattern`     | Present for "contains invalid characters" failures.           |
| `allowed_description` | Plain-language version of `allowed_pattern`.                  |
| `invalid_chars`       | Sorted list of individual characters rejected (when any exist).|
| `max_length`          | Present for "exceeds maximum length" failures.                |

### `GET /api/kg/query`

Query the knowledge graph for an entity's relationships. Returns typed facts
with temporal validity.

| Parameter   | Type   | Required | Description                                        |
|-------------|--------|----------|----------------------------------------------------|
| `entity`    | string | **yes**  | Entity to query (e.g. `Alice`, `MyProject`). See [Identifier validation](#identifier-validation). |
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

Add a fact triple to the knowledge graph. `subject`, `predicate`, and
`object` must match the rule in [Identifier validation](#identifier-validation);
violations return a structured 400.

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

Mark a fact as no longer true (e.g. job ended, moved house). `subject`,
`predicate`, and `object` follow the same rule as
[`POST /api/kg/facts`](#post-apikgfacts).

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
  ├─ 5. Truncate to 200 000 characters
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

Cap the assembled payload (header from Step 6 + Markdown body) at
**200 000 characters** — the bridge rejects anything longer with a 400. Flag
`truncated: true` in the return value so callers can note the loss.

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
