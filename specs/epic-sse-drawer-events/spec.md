# SSE event stream for drawer mutations

## Context

MemoryMark currently has no way for external local processes to react to memory writes. The user wants to trigger local processes whenever a drawer is added (and, by extension, updated/deleted), with support for **multiple subscribers** and **resumption by either event ID or timestamp** so subscribers that miss events while offline can catch up.

The bridge (`bridge/server.py`) is the sole palace writer (per architecture memory), and every mutation — REST (`POST/PUT/DELETE /api/drawers`) **and** MCP (`/mcp` → `tool_add_drawer`/`tool_delete_drawer`/`tool_diary_write`) — already flows through `mempalace.mcp_server._wal_log` **before** the underlying Chroma write. This makes the WAL a natural, already-existing event log.

Investigation findings:
- WAL path: `~/.mempalace/wal/write_log.jsonl`, append-only, mode 0o600, `O_APPEND|O_CREAT` (safe for concurrent readers).
- Format: one JSON object per line — `{"timestamp": "<ISO-8601>", "operation": "<op>", "params": {...}, "result": ...}`.
- **No sequence IDs exist today**; ISO timestamps can collide at µs resolution.
- Chroma `filed_at` metadata is not range-queryable — we cannot resume from the palace itself.
- No existing pub/sub, SSE, or WebSocket in the bridge.

## Approach

Tail the WAL in-process and fan out to SSE subscribers. Event ID = WAL line number (1-based, monotonic, durable).

### Components (all in `bridge/server.py`)

1. **WAL tailer** — async task started in `_lifespan`:
   - Opens `~/.mempalace/wal/write_log.jsonl` for reading, seeks to end (or to a replay offset).
   - Uses `asyncio.to_thread` + polling at ~100ms to read new lines (simpler and more portable than `inotify`/`fsevents`; low overhead for write-light workload).
   - For each new line: parse JSON, assign `event_id = line_number`, push to every active subscriber queue (`put_nowait`; drop-oldest on full queue).
   - Handles log rotation / truncation defensively (if file size shrinks, re-open from start).

2. **Subscriber registry** — module-level `set[asyncio.Queue]`, each queue `maxsize=256`. Each `GET /api/events` connection owns one queue; removed on disconnect.

3. **Ring buffer** — `collections.deque(maxlen=1024)` of the most recently broadcast events, for fast in-memory replay. Older replays fall back to scanning the WAL file.

4. **`GET /api/events` endpoint** — returns `StreamingResponse(media_type="text/event-stream")`:
   - Honours `Last-Event-ID` header (per SSE spec, auto-sent by browser `EventSource` on reconnect).
   - Honours `?since_timestamp=<ISO-8601>` query param as an alternative resumption mode.
   - Honours `?ops=add_drawer,update_drawer` filter (optional; default = all).
   - Replay phase: scan ring buffer first; if requested ID/timestamp is older, scan the WAL file once and stream matching entries. Then attach the subscriber's queue for live events.
   - Frame format:
     ```
     id: <line_number>
     event: <operation>
     data: {"timestamp": "...", "operation": "...", "params": {...}}

     ```
   - Heartbeat: send a `: keepalive\n\n` comment every 15s so proxies/NATs don't close idle connections.
   - On `await request.is_disconnected()`: remove queue from registry, close.

### Registration model (answer to the user's question)

Clients register **implicitly by connecting** to `GET /api/events`. No subscription endpoint, no stored subscriber list. Reconnection is transparent:

- Browser: `const es = new EventSource("http://localhost:21517/api/events"); es.onmessage = …` — `EventSource` auto-reconnects and sends `Last-Event-ID` for free.
- Python: `httpx.stream("GET", url, headers={"Last-Event-ID": str(last)})`, iterate `iter_lines()`.
- Curl: `curl -N -H "Last-Event-ID: 42" http://localhost:21517/api/events`.

### Files to modify

- `bridge/server.py` — add the tailer, registry, ring buffer, `/api/events` route (and its lifespan startup hook inside the existing `_lifespan` at line 79).

### Files to reuse (no changes)

- `mempalace.mcp_server._wal_log` — already called from every mutating path; no hook needed on our side.
- `_lifespan` in `bridge/server.py:79` — extend to start/stop the tailer task.
- `CORSMiddleware` at `bridge/server.py:98` — already allows extension origins; SSE works through it unchanged.

### Event types emitted

All WAL `operation` values — currently `add_drawer`, `update_drawer`, `delete_drawer`, `diary_write` (and any future additions appear automatically since we tail, not patch call sites).

### Ordering & delivery guarantees

- **Ordering**: strict — WAL is append-only and the tailer reads sequentially.
- **At-least-once within connection**: queue is bounded; on overflow we drop oldest and emit a synthetic `event: overrun` so the client knows to reconnect with `Last-Event-ID` for replay.
- **Across disconnect**: replay from WAL file covers the full history as long as the WAL isn't rotated away. (WAL rotation is out of scope — mempalace currently never rotates.)

## Verification

1. **Unit**: Add `bridge/test_events.py`:
   - Start `TestClient(app)`, `GET /api/events` in a background task, `POST /api/drawers`, assert an `add_drawer` frame arrives with a numeric `id:`.
   - Second subscriber connects mid-stream, receives subsequent events but not earlier ones (unless `Last-Event-ID` is set).
   - Reconnect with `Last-Event-ID: N` replays frames `>N`.
   - `?since_timestamp=…` replays frames with timestamp `≥` the value.
   - `?ops=add_drawer` filters out `update_drawer` frames.

2. **Manual end-to-end**:
   ```
   # Terminal 1
   uv run uvicorn bridge.server:app --host 127.0.0.1 --port 21517
   # Terminal 2
   curl -N http://127.0.0.1:21517/api/events
   # Terminal 3 — REST path
   curl -X POST http://127.0.0.1:21517/api/drawers \
     -H 'Content-Type: application/json' \
     -d '{"wing":"Test","room":"Events","content":"hello"}'
   # Terminal 3 — MCP path (should also surface on terminal 2)
   # call tool_add_drawer via /mcp
   ```
   Confirm terminal 2 shows both events with consecutive `id:` values.

3. **Resume**: kill the curl, re-run with `-H "Last-Event-ID: <n>"` and verify missed events replay in order.

4. **Heartbeat**: leave a subscriber idle for >15s and confirm the connection stays alive (keepalive comments visible with `curl -v`).
