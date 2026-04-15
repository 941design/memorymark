# Acceptance Criteria — SSE p1 (cursor + overflow correctness fixes)

## AC-P01: Monotonic global sequence introduced

`bridge/events.py` exports a module-level integer `_global_seq` (initially `0`).
`_global_seq` is incremented by exactly 1 for each WAL line the tailer broadcasts,
before the event is placed in the ring buffer or any subscriber queue.

## AC-P02: SSE `id:` is sourced from `_global_seq`, not WAL line number

Every event dict produced by `_wal_tailer()` has `"id": _global_seq` at the point
of broadcast.  The raw WAL `line_number` variable is retained for file-offset
tracking only and is never written into event payloads.

## AC-P03: `_global_seq` never resets on WAL truncation

When the tailer detects file shrinkage (`file_size < file_pos`), it resets
`file_pos = 0` and `line_number = 0` (file-offset concern) but leaves `_global_seq`
unchanged.  Events broadcast after the truncation carry ids strictly greater than
any id broadcast before the truncation.

## AC-P04: Ring buffer and `replay_from_id` key off `_global_seq`

`replay_from_id(last_id)` filters by `event["id"] > last_id` using the
`_global_seq`-sourced id.  The ring-buffer window check (`buf[0]["id"]`) also uses
this monotonic id, not a raw line number.

## AC-P05: `cursor-stale` signal when pre-truncation cursor falls outside ring buffer

When `replay_from_id(last_id)` is called and `last_id` is less than the oldest id
in the ring buffer (i.e. the ring buffer can no longer serve events for that cursor),
the function returns a single synthetic event:

```python
{"id": buf[0]["id"] - 1, "event": "cursor-stale", "data": {"min_id": buf[0]["id"]}}
```

followed by no further replay entries.  The SSE endpoint then streams live from
that point.  (If the ring buffer is empty, the function returns `[]` — no stale
signal needed, client will receive live events.)

## AC-P06: Truncation-recovery regression test passes

A pytest test in `bridge/test_events.py` (or `bridge/test_events_p1.py`):

1. Starts the WAL tailer against a monkeypatched temp WAL.
2. Writes 3 entries and waits until all 3 are broadcast (ids 1–3).
3. Records `pre_trunc_id = 3`.
4. Truncates the WAL to 0 bytes.
5. Writes 2 fresh entries and waits until both are broadcast (ids 4–5).
6. Calls `replay_from_id(pre_trunc_id)`.
7. Asserts the result is non-empty (either `cursor-stale` frame or events with id
   4 and 5, depending on ring-buffer window).
8. Asserts every returned event's `id` is strictly greater than `pre_trunc_id`.

## AC-P07: Queue overflow preserves the triggering event

`_put_to_queue(q, item)` when `q` is full:

1. Drops the **two** oldest entries (two `get_nowait()` calls).
2. Enqueues a synthetic `{"event": "overrun", "data": {}}` frame.
3. Enqueues `item` (the triggering event).

After this sequence the triggering event is the last item in the queue, and an
`overrun` frame immediately precedes it.

## AC-P08: Overflow regression test — newest event is present in queue

A pytest test:

1. Creates a subscriber queue and fills it to `_QUEUE_MAXSIZE` with dummy events.
2. Calls `ev.broadcast(new_event)` where `new_event` is distinct.
3. Drains the queue into a list.
4. Asserts an `overrun` frame is present in the list.
5. Asserts `new_event` (the triggering event) is the **last** item in the list.
6. Asserts no `QueueFull` exception is raised (implicit — if item is last, insert succeeded).

## AC-P09: `test_tailer_handles_wal_truncation` updated to match new cursor semantics

The existing test `test_tailer_handles_wal_truncation` asserts `second["id"] == 1`
(reset line numbering).  After the fix this assertion must be updated: the post-
truncation event's id must be `> pre_truncation_id` (i.e. `second["id"] > first["id"]`
and `second["id"] >= 2`).

## AC-P10: All 72 pre-existing tests pass without modification (except AC-P09 update)

Running `cd bridge && uv run pytest -v` shows 72 tests (plus any new p1 regression
tests) passing.  The only pre-existing test permitted to change its assertion is
`test_tailer_handles_wal_truncation` per AC-P09.

## AC-P11: `_scan_wal_by_id` WAL fallback is removed or made unreachable for in-process clients

Because `_global_seq` can exceed the number of WAL lines in the current file (after
truncation), `_scan_wal_by_id` cannot assign correct ids by re-counting lines.
The WAL scan fallback path in `replay_from_id` is either removed or guarded so
it is never invoked for a cursor that post-dates the current process start; instead
the `cursor-stale` path (AC-P05) handles out-of-window requests.

## AC-P12: Adversarial review returns `approve` or only `low`-severity findings

After both stories are merged, running `/codex:adversarial-review` on the diff
returns a verdict of `approve`, or lists only findings of severity `low` — no
remaining `high` or `medium` findings.
