# SSE drawer events — p1 (cursor + overflow correctness fixes)

## Context

The main epic `epic-sse-drawer-events` shipped an SSE event stream for drawer
mutations. An adversarial review (`/codex:adversarial-review`) flagged two
correctness defects that will cause **silent data loss** under realistic
failure modes. This mini-epic fixes both and adds regression tests. The review
verdict was `needs-attention` with one `high` and one `medium` finding — the
`high` finding is blocking for ship.

**Source of the review**: Codex adversarial-review output, preserved in
`review.md` in this epic directory for reference.

## Problem 1 (HIGH) — Cursor model breaks after WAL truncation

### Current behaviour (defective)
- `bridge/events.py:_wal_tailer()` resets `line_number = 0` when the WAL file
  shrinks (truncation/rotation detection).
- Events emitted after the reset get ids starting over at `1`.
- Clients reconnecting with `Last-Event-ID: 500` (captured before truncation)
  call `_scan_wal_by_id()` which skips every line with `id <= 500`.
- Result: new post-truncation events with ids `1..500` are **silently dropped
  for the reconnecting client** — the exact recovery path this feature is
  supposed to support.

### Required fix
Decouple the SSE `id:` value from the raw WAL line number. Introduce a
monotonic global sequence `_global_seq` that:
- Starts at 0 at process start.
- Is incremented on every broadcast (once per WAL line the tailer reads).
- **Never resets** when the WAL is truncated — the counter keeps climbing.
- Is the value emitted as `id:` in SSE frames.
- Is the value `_scan_wal_by_id()` and the ring-buffer replay logic key off.

The tailer still needs the raw WAL line number internally to know where to
resume reading the file after a truncation, but that is a file-offset concern
and must not bleed into the client-visible cursor.

The ring buffer stores events keyed by `_global_seq`. The WAL fallback scan —
when a client asks for an id older than the oldest buffered event — must
remap: walk the WAL from the beginning, assign each line its "correct"
global_seq based on (previous truncation epoch count × lines_per_epoch? NO)
— simpler: the WAL scan fallback is only meaningful for events the current
process has already broadcast. Once the process restarts, the global sequence
resets too, so WAL-fallback replay across restarts is out of scope (and was
already, since the ring buffer is in-memory anyway).

**Concretely** the fix scope is intra-process: after an in-process WAL
truncation, a reconnecting client with a pre-truncation `Last-Event-ID` must
receive subsequent events via the ring buffer (if the ids are still within
window) or a clear signal that its cursor is invalid. If the pre-truncation id
is older than the ring-buffer's oldest entry, reply with a synthetic
`event: cursor-stale` frame carrying the current `_global_seq` lower bound,
then start live streaming from that point. Clients can then choose to reset or
reconcile.

## Problem 2 (MEDIUM) — Queue overflow drops the triggering event

### Current behaviour (defective)
`bridge/events.py:_put_to_queue()` (or equivalent), when the subscriber's
`asyncio.Queue` is full:

1. Drops the oldest entry (one slot freed).
2. Enqueues an `overrun` frame (slot filled).
3. Attempts to enqueue the triggering event (queue is full again) → `QueueFull`
   is caught and the event is **silently discarded**.

Result: the subscriber is told "you lost events" but also loses the newest
event, and there is no persistent redelivery mechanism on the live stream.
Only a full reconnect-and-replay would recover it, but the client has no
reason to believe it needs to reconnect.

### Required fix
On overflow, drop **two** oldest entries before enqueuing both the overrun
marker and the triggering event, so the newest event survives. Equivalent
formulation: drop-oldest until there is room for both entries (overrun + event).

## Out of scope
- Persisting `_global_seq` across process restarts (restart resets the
  in-memory broadcaster; the existing spec already states ring buffer is
  in-memory and WAL-fallback is intra-process).
- Changing WAL file format or adding sequence IDs to WAL entries on disk.
- Rotation policy (mempalace does not rotate the WAL).
- Any protocol change visible to the REST or MCP write paths.

## Verification (must all hold)

1. **Cursor-after-truncation regression test** (`bridge/test_events.py`):
   - Start tailer against a temp WAL.
   - Write 3 events, record the last `_global_seq`.
   - Truncate the WAL file to 0 bytes.
   - Write 2 fresh events.
   - Connect a subscriber with `Last-Event-ID` set to the pre-truncation id.
   - Assert the subscriber receives the 2 post-truncation events (or a
     `cursor-stale` frame followed by them) — **not silence**.
   - Assert the emitted `id:` values are strictly greater than the
     pre-truncation cursor.

2. **Overflow preserves newest event** (`bridge/test_events.py`):
   - Force an overflow on one subscriber's queue.
   - Assert the subscriber's queue contains, in order: [dropped… , overrun
     marker, triggering event].
   - I.e., after draining, the last entry the subscriber saw is the newest
     event, and an `overrun` frame appears before it.

3. **No pre-existing test regressions**: all 72 tests from the main epic still
   pass.

4. **Adversarial review** (`/codex:adversarial-review`) returns `approve` OR
   returns only `low`-severity findings — no `high` or `medium` left
   unaddressed.

## Files in scope
- `bridge/events.py` (modify)
- `bridge/server.py` (SSE endpoint may need to read a different attribute for
  `id:` — adjust if needed)
- `bridge/test_events.py` (add regression tests; existing tests may need
  cursor-semantic updates)

## Files explicitly out of scope
- `mempalace/*` — no changes to the mempalace package.
- `pyproject.toml` — no new dependencies.
- Any other endpoint.
