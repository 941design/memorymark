# Adversarial review output (verbatim — source of truth for p1 fixes)

Target: working tree diff
Verdict: **needs-attention**

No-ship. The SSE cursor semantics break after WAL truncation, and queue
overflow drops the triggering event outright, so subscribers can silently miss
mutations under real failure conditions.

## Findings

### [HIGH] WAL truncation reuses event IDs, so resumed clients can skip all new events after a reset
**Location**: `bridge/events.py:89-205`

`_wal_tailer()` resets `line_number` to 0 when the WAL shrinks, so new events
restart at `id=1`. Replay-by-cursor still treats `Last-Event-ID` as a monotonic
global line number and `_scan_wal_by_id()` skips every line `<= last_id`. A
client that last saw a pre-truncation event like `id=500` will reconnect with
`Last-Event-ID: 500` and receive nothing from the new WAL until it accumulates
more than 500 lines.

That is silent data loss across exactly the recovery path this feature is
supposed to support.

**Recommendation**: Make the replay cursor monotonic across truncation /
rotation instead of reusing raw line numbers. For example, include a WAL
epoch/generation in the event ID, persist a global sequence, or explicitly
detect a reset and invalidate numeric resume cursors so clients can recover
safely.

### [MEDIUM] Queue overflow drops the current event that triggered the overrun
**Location**: `bridge/events.py:36-50`

When a subscriber queue is full, `_put_to_queue()` removes one old item,
enqueues an overrun frame, and then tries to enqueue the real event. With a
fixed-size queue, the overrun insert fills the freed slot, so the subsequent
`put_nowait(item)` usually fails and is silently discarded.

Under bursty writes, the subscriber loses not only backlog but also the newest
mutation that caused the overflow. If the client does not immediately
reconnect and replay, that event is never delivered on the live stream.

**Recommendation**: Reserve capacity for both the synthetic overrun signal and
the triggering event. Dropping two old items when full, or replacing the
oldest item with a compact overrun marker that includes the dropped/current
cursor, would preserve the newest event while still signaling loss.

## Next steps

- Fix the cursor model so replay remains correct after WAL truncation or
  rotation.
- Change overflow handling so the live stream still emits the triggering event
  alongside the overrun signal.
- Add regression tests that reconnect with a pre-truncation `Last-Event-ID` and
  that assert the newest event survives queue overflow.
