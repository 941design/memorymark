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
