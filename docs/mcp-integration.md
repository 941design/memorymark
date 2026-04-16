# MemoryMark MCP Integration Guide

Connecting AI agent harnesses to the MemoryMark bridge via the Model Context Protocol.

## Transport

| Property       | Value                                        |
|----------------|----------------------------------------------|
| Transport      | Streamable HTTP (JSON-RPC 2.0)               |
| Endpoint       | `http://localhost:21517/mcp`                 |
| Protocol       | MCP `2025-03-26`                             |
| Authentication | None (loopback-only by design)               |
| Session mode   | Stateless (`stateless=True`)                 |
| Response format| JSON (`json_response=True`)                  |

The bridge runs on `127.0.0.1:21517` and binds to loopback only. No API keys
or tokens are required. Each tool call is independent --- there is no
server-side session state.

## Connecting from agent harnesses

### Claude Desktop / Claude Code

Add to your MCP settings (`claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "memorymark": {
      "url": "http://localhost:21517/mcp"
    }
  }
}
```

### Cursor / Codex / Zed

Each editor exposes an MCP server configuration UI or JSON file. Point the
server URL to `http://localhost:21517/mcp` with transport type
`streamable-http`.

### Programmatic access

Any MCP client that supports Streamable HTTP can connect. The wire protocol is
standard JSON-RPC 2.0:

```
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

---

## Tools

All tools are dynamically loaded from the MemPalace library at startup.
The bridge exposes them unchanged via `tools/list` and `tools/call`.

### Palace overview

#### `mempalace_status`

Palace overview --- total drawers, wing and room counts.

**Parameters:** none

**Returns:**
```json
{
  "total_drawers": 42,
  "wings": { "Web": 12, "Code": 30 },
  "rooms": { "Captures": 8, "Architecture": 4, "..." : "..." }
}
```

---

#### `mempalace_list_wings`

List all wings with drawer counts.

**Parameters:** none

**Returns:**
```json
{
  "wings": { "Web": 12, "Code": 30 }
}
```

---

#### `mempalace_list_rooms`

List rooms within a wing, or all rooms if no wing given.

| Parameter | Type   | Required | Description                     |
|-----------|--------|----------|---------------------------------|
| `wing`    | string | no       | Wing to list rooms for          |

**Returns:**
```json
{
  "wing": "Web",
  "rooms": { "Captures": 8, "Articles": 4 }
}
```

---

#### `mempalace_get_taxonomy`

Full taxonomy: wing -> room -> drawer count.

**Parameters:** none

**Returns:**
```json
{
  "taxonomy": {
    "Web": { "Captures": 8, "Articles": 4 },
    "Code": { "Architecture": 12 }
  }
}
```

---

#### `mempalace_graph_stats`

Palace graph overview: total rooms, tunnel connections, edges between wings.

**Parameters:** none

---

### Search and discovery

#### `mempalace_search`

Semantic search. Returns verbatim drawer content with similarity scores.

| Parameter      | Type    | Required | Description                                                                       |
|----------------|---------|----------|-----------------------------------------------------------------------------------|
| `query`        | string  | **yes**  | Search keywords or question. Max 250 chars. Must contain ONLY search terms.       |
| `limit`        | integer | no       | Max results (default 5, max 100)                                                  |
| `wing`         | string  | no       | Filter by wing                                                                    |
| `room`         | string  | no       | Filter by room                                                                    |
| `max_distance` | number  | no       | Max cosine distance threshold (0=identical, 2=opposite). Default 1.5. Set 0 to disable. |
| `context`      | string  | no       | Background context (not used for embedding --- reserved for future re-ranking)    |

**Returns:** array of results, each with `id`, `content`, `metadata`, and `distance`.

---

#### `mempalace_check_duplicate`

Check if content already exists in the palace before filing.

| Parameter   | Type   | Required | Description                            |
|-------------|--------|----------|----------------------------------------|
| `content`   | string | **yes**  | Content to check                       |
| `threshold` | number | no       | Similarity threshold 0--1 (default 0.9)|

---

#### `mempalace_traverse`

Walk the palace graph from a room. Shows connected ideas across wings --- the
tunnels. Start at a room and discover connections to other wings.

| Parameter    | Type    | Required | Description                                  |
|--------------|---------|----------|----------------------------------------------|
| `start_room` | string | **yes**  | Room to start from (e.g. `chromadb-setup`)   |
| `max_hops`   | integer | no      | How many connections to follow (default 2)   |

---

#### `mempalace_find_tunnels`

Find rooms that bridge two wings --- the hallways connecting different domains.

| Parameter | Type   | Required | Description    |
|-----------|--------|----------|----------------|
| `wing_a`  | string | no       | First wing     |
| `wing_b`  | string | no       | Second wing    |

---

### Drawer CRUD

#### `mempalace_add_drawer`

File verbatim content into the palace. Checks for duplicates first.

| Parameter     | Type   | Required | Description                                    |
|---------------|--------|----------|------------------------------------------------|
| `wing`        | string | **yes**  | Wing (project name)                            |
| `room`        | string | **yes**  | Room (aspect: backend, decisions, meetings...) |
| `content`     | string | **yes**  | Verbatim content to store --- never summarized |
| `source_file` | string | no       | Where this came from                           |
| `added_by`    | string | no       | Who is filing this (default: `mcp`)            |

---

#### `mempalace_get_drawer`

Fetch a single drawer by ID. Returns full content and metadata.

| Parameter   | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `drawer_id` | string | **yes**  | ID of the drawer to fetch|

---

#### `mempalace_list_drawers`

List drawers with pagination. Optional wing/room filter. Returns IDs, wings,
rooms, and content previews.

| Parameter | Type    | Required | Description                           |
|-----------|---------|----------|---------------------------------------|
| `wing`    | string  | no       | Filter by wing                        |
| `room`    | string  | no       | Filter by room                        |
| `limit`   | integer | no       | Max results per page (default 20, max 100) |
| `offset`  | integer | no       | Offset for pagination (default 0)     |

---

#### `mempalace_update_drawer`

Update an existing drawer's content and/or metadata (wing, room). Returns error
if the drawer is not found.

| Parameter   | Type   | Required | Description                           |
|-------------|--------|----------|---------------------------------------|
| `drawer_id` | string | **yes**  | ID of the drawer to update            |
| `content`   | string | no       | New content (omit to keep existing)   |
| `wing`      | string | no       | New wing (omit to keep existing)      |
| `room`      | string | no       | New room (omit to keep existing)      |

---

#### `mempalace_delete_drawer`

Delete a drawer by ID. Irreversible.

| Parameter   | Type   | Required | Description                |
|-------------|--------|----------|----------------------------|
| `drawer_id` | string | **yes**  | ID of the drawer to delete |

---

### Knowledge graph

#### `mempalace_kg_query`

Query the knowledge graph for an entity's relationships. Returns typed facts
with temporal validity.

| Parameter   | Type   | Required | Description                                              |
|-------------|--------|----------|----------------------------------------------------------|
| `entity`    | string | **yes**  | Entity to query (e.g. `Max`, `MyProject`, `Alice`)       |
| `as_of`     | string | no       | Date filter --- only facts valid at this date (YYYY-MM-DD)|
| `direction` | string | no       | `outgoing`, `incoming`, or `both` (default: `both`)      |

---

#### `mempalace_kg_add`

Add a fact to the knowledge graph. Subject -> predicate -> object with optional
time window.

| Parameter       | Type   | Required | Description                                        |
|-----------------|--------|----------|----------------------------------------------------|
| `subject`       | string | **yes**  | The entity doing/being something                   |
| `predicate`     | string | **yes**  | Relationship type (e.g. `loves`, `works_on`)       |
| `object`        | string | **yes**  | The entity being connected to                      |
| `valid_from`    | string | no       | When this became true (YYYY-MM-DD)                 |
| `source_closet` | string | no       | Closet ID where this fact appears                  |

---

#### `mempalace_kg_invalidate`

Mark a fact as no longer true.

| Parameter   | Type   | Required | Description                                  |
|-------------|--------|----------|----------------------------------------------|
| `subject`   | string | **yes**  | Entity                                       |
| `predicate` | string | **yes**  | Relationship                                 |
| `object`    | string | **yes**  | Connected entity                             |
| `ended`     | string | no       | When it stopped being true (YYYY-MM-DD, default: today) |

---

#### `mempalace_kg_timeline`

Chronological timeline of facts. Shows the story of an entity in order.

| Parameter | Type   | Required | Description                                          |
|-----------|--------|----------|------------------------------------------------------|
| `entity`  | string | no       | Entity to get timeline for (omit for full timeline)  |

---

#### `mempalace_kg_stats`

Knowledge graph overview: entities, triples, current vs expired facts,
relationship types.

**Parameters:** none

---

### Agent diary

#### `mempalace_diary_write`

Write to your personal agent diary in AAAK format. Each agent has their own
diary with full history.

| Parameter    | Type   | Required | Description                               |
|--------------|--------|----------|-------------------------------------------|
| `agent_name` | string | **yes**  | Your name --- each agent gets their own diary wing |
| `entry`      | string | **yes**  | Diary entry in AAAK format                |
| `topic`      | string | no       | Topic tag (default: `general`)            |

---

#### `mempalace_diary_read`

Read your recent diary entries. See what past versions of yourself recorded.

| Parameter    | Type    | Required | Description                           |
|--------------|---------|----------|---------------------------------------|
| `agent_name` | string  | **yes**  | Your name                             |
| `last_n`     | integer | no       | Number of recent entries (default 10) |

---

### AAAK specification

#### `mempalace_get_aaak_spec`

Get the AAAK dialect specification --- the compressed memory format MemPalace
uses. Call this if you need to read or write AAAK-compressed memories.

**Parameters:** none

---

### Housekeeping

#### `mempalace_hook_settings`

Get or set hook behavior. Call with no arguments to view current settings.

| Parameter       | Type    | Required | Description                                |
|-----------------|---------|----------|--------------------------------------------|
| `silent_save`   | boolean | no       | `true` = silent direct save, `false` = legacy blocking |
| `desktop_toast` | boolean | no       | `true` = show desktop notification         |

---

#### `mempalace_memories_filed_away`

Check if a recent palace checkpoint was saved. Returns message count and
timestamp.

**Parameters:** none

---

#### `mempalace_reconnect`

Force reconnect to the palace database. Use after external scripts or CLI
commands modified the palace directly, which can leave the in-memory HNSW index
stale.

**Parameters:** none

---

## Error handling

Tool errors are returned as standard MCP error responses:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32603,
    "message": "Drawer not found: drawer_Web_Captures_abc123"
  }
}
```

Successful tool calls return `TextContent` with JSON text:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      { "type": "text", "text": "{\"total_drawers\": 42, ...}" }
    ]
  }
}
```

The `text` field contains a JSON-encoded string. Parse it to get the tool's
structured response.

## CORS note

The bridge's CORS policy only allows `chrome-extension://` and
`moz-extension://` origins. MCP clients connect over Streamable HTTP without
browser CORS restrictions, so this does not affect agent harnesses. If you are
building a web-based MCP client running in a browser tab, it will be blocked by
CORS.
