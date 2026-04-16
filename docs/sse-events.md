# MemoryMark SSE Event Stream Guide

Consuming real-time palace mutations via Server-Sent Events.

## Endpoint

```
GET http://localhost:21517/api/events
```

| Property          | Value                              |
|-------------------|------------------------------------|
| Content-Type      | `text/event-stream`                |
| Authentication    | None (loopback-only)               |
| Heartbeat         | `: keepalive` comment every 15 s   |
| Reconnect support | `Last-Event-ID` header             |
| CORS              | `chrome-extension://` and `moz-extension://` origins only |

## Quick start

### Browser (EventSource API)

```javascript
const es = new EventSource("http://localhost:21517/api/events");

es.addEventListener("add_drawer", (e) => {
  const data = JSON.parse(e.data);
  console.log("New drawer:", data.params.drawer_id);
});

es.addEventListener("update_drawer", (e) => {
  const data = JSON.parse(e.data);
  console.log("Updated:", data.params.drawer_id);
});

es.addEventListener("delete_drawer", (e) => {
  const data = JSON.parse(e.data);
  console.log("Deleted:", data.params.drawer_id);
});

es.onerror = () => console.warn("SSE connection lost; browser will auto-reconnect");
```

> **Note:** The `EventSource` API automatically sends `Last-Event-ID` on
> reconnect, so you get seamless catch-up with no extra code.

### Node.js / fetch

```javascript
const resp = await fetch("http://localhost:21517/api/events");
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  // Split on double newline (SSE frame boundary)
  const frames = buffer.split("\n\n");
  buffer = frames.pop(); // keep incomplete trailing frame

  for (const frame of frames) {
    if (frame.startsWith(":")) continue; // comment (keepalive)
    const lines = frame.split("\n");
    const id    = lines.find(l => l.startsWith("id: "))?.slice(4);
    const event = lines.find(l => l.startsWith("event: "))?.slice(7);
    const data  = lines.find(l => l.startsWith("data: "))?.slice(6);
    if (event && data) {
      console.log({ id, event, data: JSON.parse(data) });
    }
  }
}
```

### curl

```bash
curl -sN http://localhost:21517/api/events
```

Or use the project Makefile:

```bash
make events                    # live tail
make events LAST_ID=42         # resume after event 42
```

---

## Frame format

Each event is a standard SSE frame with three fields:

```
id: <integer>
event: <operation_name>
data: <JSON object>

```

A blank line terminates each frame. Example:

```
id: 17
event: add_drawer
data: {"timestamp":"2026-04-15T10:00:00.123456","operation":"add_drawer","params":{"drawer_id":"drawer_Web_Captures_a1b2c3d4e5f6a1b2c3d4e5f6","wing":"Web","room":"Captures","added_by":"memorymark","content_length":5000,"content_preview":"# Page Title\n\nFirst 200 characters of content...","source_url":"https://example.com/page","content_hash":"e3b0c44298fc1c149afbf4c8996fb924..."}}

```

### Data envelope

Every `data` payload has the same envelope:

```json
{
  "timestamp": "<ISO-8601>",
  "operation": "<operation_name>",
  "params": { }
}
```

| Field       | Type   | Description                                          |
|-------------|--------|------------------------------------------------------|
| `timestamp` | string | ISO-8601 time when the mutation occurred             |
| `operation` | string | Operation name (same value as the SSE `event` field) |
| `params`    | object | Operation-specific payload (see below)               |

---

## Event types

Events are generated from the MemPalace write-ahead log (WAL). Any operation
that writes to the WAL produces an event. The set of event types is open-ended
--- new MemPalace operations may appear without a bridge update. The core types
you will encounter are documented below.

### `add_drawer`

Fired when a new drawer is created (via REST `POST /api/drawers` or MCP
`mempalace_add_drawer`).

```json
{
  "timestamp": "2026-04-15T10:00:00.123456",
  "operation": "add_drawer",
  "params": {
    "drawer_id": "drawer_Web_Captures_a1b2c3d4e5f6a1b2c3d4e5f6",
    "wing": "Web",
    "room": "Captures",
    "added_by": "memorymark",
    "content_length": 5000,
    "content_preview": "# Page Title\n\nFirst 200 chars...",
    "source_url": "https://example.com/page",
    "content_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
}
```

| Param             | Type    | Description                                    |
|-------------------|---------|------------------------------------------------|
| `drawer_id`       | string  | Content-addressed drawer ID                    |
| `wing`            | string  | Wing the drawer was filed into                 |
| `room`            | string  | Room the drawer was filed into                 |
| `added_by`        | string  | Creator (`memorymark`, `mcp`, or agent name)   |
| `content_length`  | integer | Full content length in characters              |
| `content_preview` | string  | First 200 characters of content                |
| `source_url`      | string  | Source URL of the captured page (empty string if none) |
| `content_hash`    | string  | SHA-256 hex digest of the full content         |

### `update_drawer`

Fired when a drawer's content or metadata is modified.

```json
{
  "timestamp": "2026-04-15T10:05:00.000000",
  "operation": "update_drawer",
  "params": {
    "drawer_id": "drawer_Web_Captures_a1b2c3d4e5f6a1b2c3d4e5f6",
    "old_wing": "Web",
    "old_room": "Captures",
    "new_wing": "Web",
    "new_room": "Articles",
    "content_changed": true,
    "content_preview": "# Updated Title\n\nNew content...",
    "added_by": "memorymark",
    "source_url": "https://example.com/page",
    "content_hash": "a1b2c3d4e5f6..."
  }
}
```

| Param             | Type    | Description                                    |
|-------------------|---------|------------------------------------------------|
| `drawer_id`       | string  | Drawer that was updated                        |
| `old_wing`        | string  | Previous wing                                  |
| `old_room`        | string  | Previous room                                  |
| `new_wing`        | string  | New wing (same as old if unchanged)            |
| `new_room`        | string  | New room (same as old if unchanged)            |
| `content_changed` | boolean | Whether content itself was modified            |
| `content_preview` | string  | First 200 chars of current content (always present) |
| `added_by`        | string  | Stored creator of the drawer (preserved across updates) |
| `source_url`      | string  | Source URL (reflects latest metadata)          |
| `content_hash`    | string  | SHA-256 hex digest of current content          |

### `delete_drawer`

Fired when a drawer is deleted (via REST `DELETE /api/drawers/{id}` or MCP
`mempalace_delete_drawer`).

The `params` shape depends on the MemPalace WAL entry. At minimum, expect
`drawer_id`.

### `diary_write`

Fired when an agent writes a diary entry via `POST /api/diary/write` (REST) or
`mempalace_diary_write` (MCP).

```json
{
  "timestamp": "2026-04-16T10:15:30.123456",
  "operation": "diary_write",
  "params": {
    "agent_name": "daydreamer",
    "topic": "synthesis",
    "entry_id": "diary_wing_daydreamer_20260416_101530_abc123def456",
    "entry_preview": "SESSION:2026-04-16|observed.drawer.drift|..."
  }
}
```

| Param           | Type   | Description                                       |
|-----------------|--------|---------------------------------------------------|
| `agent_name`    | string | Agent identity (its own wing within the palace)   |
| `topic`         | string | Logical topic bucket (default `general`)          |
| `entry_id`      | string | Deterministic diary entry ID                      |
| `entry_preview` | string | First 200 characters of the diary entry           |

### Other operations

Any MemPalace operation that logs to the WAL generates an SSE event. Knowledge
graph mutations (`kg_add`, `kg_invalidate`) and other future operations will
appear with their WAL operation name as the SSE `event` field. Your client
should handle unknown event types gracefully.

---

## Synthetic events

The bridge emits two synthetic event types that are not WAL entries. They signal
stream health issues.

### `overrun`

Emitted when the server-side subscriber queue (256 items) overflows. The client
is consuming events too slowly.

```
id: 100
event: overrun
data: {}
```

**What to do:** The client missed events. Use the `id` from the most recent
successfully processed event and reconnect with `Last-Event-ID` to replay from
the ring buffer.

### `cursor-stale`

Emitted during replay when the requested `Last-Event-ID` is older than the ring
buffer's oldest entry (ring buffer holds the last 1024 events).

```
id: 500
event: cursor-stale
data: {"min_id": 501}
```

| Param    | Type    | Description                                        |
|----------|---------|----------------------------------------------------|
| `min_id` | integer | Oldest event ID available in the ring buffer       |

**What to do:** The client's cursor is too old for seamless replay. Either
accept the gap and continue from `min_id`, or use `since_timestamp` to replay
from the full WAL file.

---

## Query parameters

### `ops` --- filter by operation type

Restrict the stream to specific operations. Comma-separated.

```
GET /api/events?ops=add_drawer,delete_drawer
```

Only `add_drawer` and `delete_drawer` events will be delivered. All others are
silently dropped. Applies to both replay and live phases.

### `since_timestamp` --- replay from a point in time

Replay events from a specific timestamp onward, then continue with live
streaming.

```
GET /api/events?since_timestamp=2026-04-15T00:00:00.000000
```

The timestamp format is ISO-8601 with microsecond precision. Comparison is
lexicographic. Events with `timestamp >= since_timestamp` are included.

The server checks the in-memory ring buffer first. If the requested timestamp
predates the buffer, it falls back to a single sequential scan of the WAL file.

---

## Reconnection and replay

The stream supports two replay mechanisms that ensure clients can recover from
disconnections without missing events.

### Via `Last-Event-ID` (recommended)

The SSE spec defines the `Last-Event-ID` header. Browsers send it
automatically on reconnect when using `EventSource`.

```
GET /api/events
Last-Event-ID: 42
```

The server replays all events with `id > 42` from the in-memory ring buffer
(last 1024 events), then transitions to live streaming. If `42` is older than
the ring buffer, a `cursor-stale` frame is emitted first.

### Via `since_timestamp`

For clients that track time rather than event IDs:

```
GET /api/events?since_timestamp=2026-04-15T10:00:00.000000
```

This scans the ring buffer or WAL file as needed. Use this when resuming after a
long disconnection where the event ID space may have rotated out of the ring
buffer.

### Precedence

If both `Last-Event-ID` header and `since_timestamp` parameter are present,
`Last-Event-ID` takes priority.

---

## Heartbeat

The server sends an SSE comment frame every 15 seconds when idle:

```
: keepalive

```

This keeps the TCP connection alive through proxies and NAT gateways. SSE
clients should ignore comment frames (the `EventSource` API does this
automatically).

---

## Architecture notes

```
 REST POST/PUT/DELETE     MCP tools/call
        |                       |
        v                       v
   +-----------+          +-----------+
   | FastAPI   |          | MCP Server|
   | /api/*    |          | /mcp      |
   +-----------+          +-----------+
        |                       |
        +--------> WAL <--------+
                    |
            WAL tailer (100ms poll)
                    |
              Ring buffer (1024)
                    |
           Subscriber queues (256 each)
                    |
              GET /api/events
                    |
                 Clients
```

1. Any mutation (REST or MCP) appends a JSON line to the MemPalace WAL file.
2. The WAL tailer polls the file every 100 ms, parses new lines, assigns
   monotonic event IDs (`_global_seq`), and broadcasts to all subscriber queues.
3. The ring buffer (deque, maxlen=1024) retains recent events for fast replay.
4. Each SSE client gets its own `asyncio.Queue` (maxsize=256). Events are
   delivered via the queue; heartbeats fill idle gaps.
5. On disconnect, the subscriber queue is removed. No server-side state persists
   between connections.

### Event ID guarantees

- IDs are monotonically increasing integers starting from 1.
- IDs never reset, even if the WAL file is truncated or rotated.
- IDs are scoped to the bridge process lifetime. A bridge restart resets the
  counter to 0 (the WAL file itself remains durable).

### Idempotency

Drawer IDs are content-addressed: `drawer_{wing}_{room}_{sha256(wing+room+content)[:24]}`.
Creating the same content twice produces the same drawer ID. The second
`POST /api/drawers` returns success without writing a new WAL entry, so no
duplicate SSE event is emitted.
