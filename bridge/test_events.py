"""Tests for stories 01 and 02: WAL tailer, broadcaster, and SSE endpoint."""

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

import mempalace.mcp_server as _mp
import events as ev
import server as srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_wal_entry(entry: dict) -> None:
    """Append a JSON entry to the current (monkeypatched) WAL file."""
    wal_file = _mp._WAL_FILE
    with open(str(wal_file), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _make_entry(operation: str = "add_drawer", **params) -> dict:
    return {
        "timestamp": "2026-04-15T10:00:00.000000",
        "operation": operation,
        "params": params or {"drawer_id": "d1"},
        "result": None,
    }


def _run(coro):
    """Run a coroutine in a new event loop (tests are sync)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixture: clean events state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_events_state():
    """Reset module-level state in events.py before each test."""
    ev._subscribers.clear()
    ev._ring_buffer.clear()
    ev._tailer_task = None
    ev._event_loop = None
    ev._global_seq = 0
    yield
    # Ensure no lingering tasks
    if ev._tailer_task is not None:
        _run(ev.stop_tailer())
    ev._subscribers.clear()
    ev._ring_buffer.clear()
    ev._event_loop = None
    ev._global_seq = 0


# ---------------------------------------------------------------------------
# Tests: subscribe / unsubscribe
# ---------------------------------------------------------------------------


def test_subscribe_returns_queue_added_to_registry():
    q = ev.subscribe()
    assert q in ev._subscribers
    ev.unsubscribe(q)


def test_unsubscribe_removes_queue():
    q = ev.subscribe()
    ev.unsubscribe(q)
    assert q not in ev._subscribers


def test_unsubscribe_nonexistent_is_safe():
    q: asyncio.Queue = asyncio.Queue()
    ev.unsubscribe(q)  # should not raise


# ---------------------------------------------------------------------------
# Tests: broadcast
# ---------------------------------------------------------------------------


def test_broadcast_pushes_to_ring_buffer():
    event = {"id": 1, "event": "add_drawer", "data": {"timestamp": "t", "operation": "add_drawer", "params": {}}}
    ev.broadcast(event)
    assert event in ev._ring_buffer


def test_broadcast_delivers_to_subscriber():
    q = ev.subscribe()
    event = {"id": 1, "event": "add_drawer", "data": {}}
    ev.broadcast(event)
    received = q.get_nowait()
    assert received is event
    ev.unsubscribe(q)


def test_broadcast_delivers_to_multiple_subscribers():
    q1 = ev.subscribe()
    q2 = ev.subscribe()
    event = {"id": 2, "event": "delete_drawer", "data": {}}
    ev.broadcast(event)
    assert q1.get_nowait() is event
    assert q2.get_nowait() is event
    ev.unsubscribe(q1)
    ev.unsubscribe(q2)


def test_broadcast_overrun_drops_oldest_and_inserts_overrun_frame():
    q = ev.subscribe()
    # Fill queue to maxsize
    for i in range(ev._QUEUE_MAXSIZE):
        q.put_nowait({"id": i})
    assert q.full()

    new_event = {"id": ev._QUEUE_MAXSIZE + 1, "event": "add_drawer", "data": {}}
    ev.broadcast(new_event)

    items = []
    while not q.empty():
        items.append(q.get_nowait())

    # There should be an overrun frame somewhere
    overrun_frames = [x for x in items if x.get("event") == "overrun"]
    assert len(overrun_frames) >= 1

    ev.unsubscribe(q)


def test_overflow_preserves_newest_event():
    """AC-P08: Overflow drops two oldest; overrun frame immediately precedes triggering event."""
    q = ev.subscribe()
    for i in range(ev._QUEUE_MAXSIZE):
        q.put_nowait({"id": i})
    assert q.full()

    new_event = {"id": ev._QUEUE_MAXSIZE + 1, "event": "add_drawer", "data": {}}
    ev.broadcast(new_event)

    items = []
    while not q.empty():
        items.append(q.get_nowait())

    # new_event must be the last item
    assert items[-1] is new_event, "Triggering event must be last item in queue"
    # overrun frame must be immediately before it
    assert items[-2].get("event") == "overrun", "Overrun frame must immediately precede triggering event"

    ev.unsubscribe(q)


# ---------------------------------------------------------------------------
# Tests: ring buffer
# ---------------------------------------------------------------------------


def test_ring_buffer_maxsize():
    for i in range(ev._RING_MAXSIZE + 10):
        ev.broadcast({"id": i, "event": "add_drawer", "data": {}})
    assert len(ev._ring_buffer) == ev._RING_MAXSIZE


# ---------------------------------------------------------------------------
# Tests: WAL tailer task lifecycle
# ---------------------------------------------------------------------------


async def _start_and_stop_tailer():
    await ev.start_tailer()
    assert ev._tailer_task is not None
    assert not ev._tailer_task.done()
    await ev.stop_tailer()
    assert ev._tailer_task is None


def test_tailer_task_lifecycle():
    _run(_start_and_stop_tailer())


# ---------------------------------------------------------------------------
# Tests: tailer reads WAL entries and broadcasts them
# ---------------------------------------------------------------------------


async def _tailer_picks_up_wal_entry():
    q = ev.subscribe()
    await ev.start_tailer()

    # Write a WAL entry
    _write_wal_entry(_make_entry("add_drawer", drawer_id="d1"))

    # Poll until event arrives (up to 2s)
    deadline = time.monotonic() + 2.0
    received = None
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if not q.empty():
            received = q.get_nowait()
            break

    await ev.stop_tailer()
    ev.unsubscribe(q)
    return received


def test_tailer_picks_up_wal_entry():
    received = _run(_tailer_picks_up_wal_entry())
    assert received is not None
    assert received["event"] == "add_drawer"
    assert received["id"] == 1
    data = received["data"]
    assert data["operation"] == "add_drawer"
    assert "timestamp" in data
    assert "params" in data


async def _tailer_assigns_monotonic_ids():
    q = ev.subscribe()
    await ev.start_tailer()

    _write_wal_entry(_make_entry("add_drawer", drawer_id="d1"))
    _write_wal_entry(_make_entry("update_drawer", drawer_id="d1"))

    events = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and len(events) < 2:
        await asyncio.sleep(0.05)
        while not q.empty():
            events.append(q.get_nowait())

    await ev.stop_tailer()
    ev.unsubscribe(q)
    return events


def test_tailer_assigns_monotonic_ids():
    events = _run(_tailer_assigns_monotonic_ids())
    assert len(events) == 2
    assert events[0]["id"] == 1
    assert events[1]["id"] == 2


# ---------------------------------------------------------------------------
# Tests: WAL truncation handling
# ---------------------------------------------------------------------------


async def _tailer_handles_wal_truncation():
    q = ev.subscribe()
    await ev.start_tailer()

    # Write first entry and wait for it
    _write_wal_entry(_make_entry("add_drawer", drawer_id="d1"))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and q.empty():
        await asyncio.sleep(0.05)

    first = q.get_nowait() if not q.empty() else None

    # Truncate WAL (simulate rotation)
    wal_path = str(_mp._WAL_FILE)
    with open(wal_path, "w") as fh:
        fh.write("")  # truncate

    # Allow tailer to detect truncation
    await asyncio.sleep(0.3)

    # Write a new entry after truncation
    _write_wal_entry(_make_entry("delete_drawer", drawer_id="d2"))

    # Wait for new event
    deadline = time.monotonic() + 2.0
    second = None
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if not q.empty():
            second = q.get_nowait()
            break

    await ev.stop_tailer()
    ev.unsubscribe(q)
    return first, second


def test_tailer_handles_wal_truncation():
    first, second = _run(_tailer_handles_wal_truncation())
    assert first is not None
    assert first["event"] == "add_drawer"
    assert second is not None
    assert second["event"] == "delete_drawer"
    # _global_seq never resets; post-truncation id must exceed pre-truncation id
    assert second["id"] > first["id"]
    assert second["id"] >= 2


# ---------------------------------------------------------------------------
# Tests: event field structure (AC-005 precursor)
# ---------------------------------------------------------------------------


async def _tailer_event_has_required_fields():
    q = ev.subscribe()
    await ev.start_tailer()

    entry = {
        "timestamp": "2026-04-15T10:00:00.123456",
        "operation": "diary_write",
        "params": {"wing": "Test", "room": "Diary"},
        "result": {"success": True},
    }
    _write_wal_entry(entry)

    deadline = time.monotonic() + 2.0
    received = None
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if not q.empty():
            received = q.get_nowait()
            break

    await ev.stop_tailer()
    ev.unsubscribe(q)
    return received


def test_tailer_event_has_required_fields():
    received = _run(_tailer_event_has_required_fields())
    assert received is not None
    assert "id" in received
    assert "event" in received
    assert "data" in received
    data = received["data"]
    assert "timestamp" in data
    assert "operation" in data
    assert "params" in data
    assert data["timestamp"] == "2026-04-15T10:00:00.123456"
    assert data["operation"] == "diary_write"
    assert data["params"] == {"wing": "Test", "room": "Diary"}


# ---------------------------------------------------------------------------
# Tests: lifespan wiring (AC-001, AC-002) via TestClient
# ---------------------------------------------------------------------------


def test_lifespan_starts_and_stops_tailer(client):
    """Verify that after lifespan starts, the tailer task was created."""
    # The client fixture runs the full lifespan; if start_tailer or stop_tailer
    # raised, the client would have failed to initialize. This test is a smoke
    # check that the app wires up without error.
    resp = client.get("/api/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Story 02: SSE endpoint tests (AC-003, AC-004, AC-005, AC-011, AC-012, AC-013)
#
# TestClient buffers the entire response before returning, so it cannot stream
# SSE. These tests use a real uvicorn server in a background thread instead.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _live_server():
    """Start the bridge app on a random port, yield the base URL, then stop."""
    srv._SSE_HEARTBEAT_INTERVAL = 0.4
    srv._SSE_POLL_TIMEOUT = 0.05

    port = _free_port()
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait until the server is ready
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start in time"

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        # Reset timings to defaults
        srv._SSE_HEARTBEAT_INTERVAL = 15.0
        srv._SSE_POLL_TIMEOUT = 1.0


def _make_broadcast_event(line_no: int = 1, operation: str = "add_drawer") -> dict:
    return {
        "id": line_no,
        "event": operation,
        "data": {
            "timestamp": "2026-04-15T10:00:00.000000",
            "operation": operation,
            "params": {"drawer_id": "test"},
        },
    }


def _broadcast_after_delay(events_to_send: list[dict], delay: float = 0.15) -> threading.Thread:
    """Return a started daemon thread that broadcasts events after delay."""
    def _run():
        time.sleep(delay)
        for evt in events_to_send:
            ev.broadcast(evt)
            time.sleep(0.02)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _collect_sse_lines(base_url: str, n_frames: int, broadcaster: threading.Thread | None = None) -> list[str]:
    """Connect to /api/events, collect until n_frames complete SSE frames received.

    A complete frame ends with a blank line after the data: field.
    Uses httpx.stream for true streaming (not buffered like TestClient).
    """
    collected: list[str] = []
    with httpx.stream("GET", f"{base_url}/api/events", timeout=10.0) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        frame_count = 0
        in_frame = False
        for line in resp.iter_lines():
            collected.append(line)
            if line.startswith("id:"):
                in_frame = True
            elif line == "" and in_frame:
                # Blank line ends the current frame
                frame_count += 1
                in_frame = False
                if frame_count >= n_frames:
                    break
    if broadcaster:
        broadcaster.join(timeout=3.0)
    return collected


def test_sse_endpoint_returns_200_text_event_stream():
    """AC-003: GET /api/events returns 200 with Content-Type text/event-stream."""
    with _live_server() as base_url:
        t = _broadcast_after_delay([_make_broadcast_event(1)])
        lines = _collect_sse_lines(base_url, n_frames=1, broadcaster=t)
    assert any(ln.startswith("id:") for ln in lines)


def test_sse_frame_structure():
    """AC-004: Each frame contains id:, event:, data: on consecutive lines."""
    with _live_server() as base_url:
        t = _broadcast_after_delay([_make_broadcast_event(1)])
        lines = _collect_sse_lines(base_url, n_frames=1, broadcaster=t)
    id_idx = next((i for i, ln in enumerate(lines) if ln.startswith("id:")), None)
    assert id_idx is not None, "No id: line found"
    assert id_idx + 2 < len(lines), f"Frame truncated, lines: {lines}"
    assert lines[id_idx].startswith("id:")
    assert lines[id_idx + 1].startswith("event:")
    assert lines[id_idx + 2].startswith("data:")


def test_sse_frame_id_is_numeric():
    """AC-004: id: value is a positive integer."""
    with _live_server() as base_url:
        t = _broadcast_after_delay([_make_broadcast_event(42)])
        lines = _collect_sse_lines(base_url, n_frames=1, broadcaster=t)
    id_line = next(ln for ln in lines if ln.startswith("id:"))
    id_val = id_line.split(":", 1)[1].strip()
    assert id_val.isdigit() and int(id_val) >= 1


def test_sse_data_contains_required_keys():
    """AC-005: data: JSON contains timestamp, operation, params."""
    with _live_server() as base_url:
        t = _broadcast_after_delay([_make_broadcast_event(1, "add_drawer")])
        lines = _collect_sse_lines(base_url, n_frames=1, broadcaster=t)
    data_line = next(ln for ln in lines if ln.startswith("data:"))
    payload = json.loads(data_line.split(":", 1)[1].strip())
    assert "timestamp" in payload
    assert "operation" in payload
    assert "params" in payload
    assert payload["operation"] == "add_drawer"


def test_sse_all_operations_emitted_when_no_filter():
    """AC-011: Without ?ops= all events pass through."""
    events_to_send = [
        _make_broadcast_event(1, "add_drawer"),
        _make_broadcast_event(2, "update_drawer"),
    ]
    with _live_server() as base_url:
        t = _broadcast_after_delay(events_to_send)
        lines = _collect_sse_lines(base_url, n_frames=2, broadcaster=t)
    event_lines = [ln for ln in lines if ln.startswith("event:")]
    ops = [ln.split(":", 1)[1].strip() for ln in event_lines]
    assert "add_drawer" in ops
    assert "update_drawer" in ops


def test_sse_heartbeat_emitted():
    """AC-012: keepalive comment emitted when no event within heartbeat interval."""
    collected: list[str] = []
    with _live_server() as base_url:
        with httpx.stream("GET", f"{base_url}/api/events", timeout=5.0) as resp:
            for line in resp.iter_lines():
                collected.append(line)
                if line.startswith(": keepalive"):
                    break
                if len(collected) > 50:
                    break
    assert any(ln.startswith(": keepalive") for ln in collected), (
        f"No keepalive found in: {collected}"
    )


def test_sse_subscriber_cleaned_up_on_disconnect():
    """AC-013: After stream ends, subscriber queue is removed from registry."""
    with _live_server() as base_url:
        initial_count = len(ev._subscribers)
        t = _broadcast_after_delay([_make_broadcast_event(1)])
        with httpx.stream("GET", f"{base_url}/api/events", timeout=5.0) as resp:
            for line in resp.iter_lines():
                if line.startswith("id:"):
                    break
        t.join(timeout=3.0)
        time.sleep(0.5)
        final_count = len(ev._subscribers)
    assert final_count == initial_count


# ---------------------------------------------------------------------------
# Story 03: Resumption and filter tests (AC-007, AC-008, AC-009, AC-010, AC-020)
# ---------------------------------------------------------------------------


def _collect_sse_frames(base_url: str, n_frames: int, headers: dict | None = None,
                        params: dict | None = None, broadcaster: threading.Thread | None = None,
                        timeout: float = 10.0) -> list[dict]:
    """Collect n_frames complete SSE frames, returning parsed dicts with id/event/data keys."""
    frames: list[dict] = []
    url = f"{base_url}/api/events"
    with httpx.stream("GET", url, headers=headers or {}, params=params or {}, timeout=timeout) as resp:
        assert resp.status_code == 200
        current: dict = {}
        for line in resp.iter_lines():
            if line.startswith("id:"):
                current["id"] = line.split(":", 1)[1].strip()
            elif line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = line.split(":", 1)[1].strip()
            elif line == "" and current:
                frames.append(current)
                current = {}
                if len(frames) >= n_frames:
                    break
    if broadcaster:
        broadcaster.join(timeout=3.0)
    return frames


def test_replay_from_id_via_last_event_id_header():
    """AC-007: Last-Event-ID header causes replay of WAL events with id > N."""
    # Pre-populate ring buffer with 3 events
    for i in range(1, 4):
        ev.broadcast(_make_broadcast_event(i, "add_drawer"))

    with _live_server() as base_url:
        # Request replay from id=1 — expect events 2 and 3 replayed
        frames = _collect_sse_frames(
            base_url, n_frames=2,
            headers={"last-event-id": "1"},
        )
    ids = [int(f["id"]) for f in frames]
    # Replayed events 2 and 3
    assert 2 in ids
    assert 3 in ids


def test_replay_served_from_ring_buffer_when_within_window():
    """AC-008: Replay from ring buffer when last_id >= buf[0].id - 1."""
    # Populate ring buffer
    for i in range(1, 6):
        ev.broadcast(_make_broadcast_event(i, "add_drawer"))

    with _live_server() as base_url:
        # Collect only the 5 replayed frames from the ring buffer
        frames = _collect_sse_frames(
            base_url, n_frames=5,
            headers={"last-event-id": "0"},
        )
    ids = [int(f["id"]) for f in frames]
    # All 5 ring-buffer events replayed
    assert all(i in ids for i in range(1, 6))


def test_replay_empty_ring_buffer_streams_live(tmp_path, monkeypatch):
    """AC-P11: WAL scan fallback removed; empty ring buffer returns no replay, client streams live."""
    wal_file = tmp_path / "write_log.jsonl"
    monkeypatch.setattr(_mp, "_WAL_FILE", wal_file)

    # Ring buffer is empty — replay_from_id returns [] and client gets live events only
    live_event = _make_broadcast_event(1, "add_drawer")
    with _live_server() as base_url:
        t = _broadcast_after_delay([live_event], delay=0.15)
        frames = _collect_sse_frames(
            base_url, n_frames=1,
            headers={"last-event-id": "0"},
            broadcaster=t,
        )
    assert len(frames) == 1
    assert frames[0]["event"] == "add_drawer"


def test_replay_from_timestamp():
    """AC-009: ?since_timestamp= replays events with timestamp >= value from ring buffer.

    Uses since_ts <= buf[0].timestamp so ring-buffer path is exercised (no WAL fallback).
    """
    events_to_load = [
        {"id": 1, "event": "add_drawer", "data": {"timestamp": "2026-04-15T10:00:00.000000", "operation": "add_drawer", "params": {}}},
        {"id": 2, "event": "add_drawer", "data": {"timestamp": "2026-04-15T11:00:00.000000", "operation": "add_drawer", "params": {}}},
        {"id": 3, "event": "add_drawer", "data": {"timestamp": "2026-04-15T12:00:00.000000", "operation": "add_drawer", "params": {}}},
    ]
    for evt in events_to_load:
        ev.broadcast(evt)

    with _live_server() as base_url:
        # since_ts = buf[0].timestamp (10:00) so ring-buffer path fires and all 3 events match
        frames = _collect_sse_frames(
            base_url, n_frames=3,
            params={"since_timestamp": "2026-04-15T10:00:00.000000"},
        )

    ids = [int(f["id"]) for f in frames]
    assert 1 in ids
    assert 2 in ids
    assert 3 in ids


def test_ops_filter_excludes_unmatched_operations():
    """AC-010: ?ops=add_drawer only delivers add_drawer events."""
    events_to_send = [
        _make_broadcast_event(1, "add_drawer"),
        _make_broadcast_event(2, "update_drawer"),
        _make_broadcast_event(3, "delete_drawer"),
        _make_broadcast_event(4, "add_drawer"),
    ]
    with _live_server() as base_url:
        t = _broadcast_after_delay(events_to_send, delay=0.1)
        frames = _collect_sse_frames(
            base_url, n_frames=2,
            params={"ops": "add_drawer"},
            broadcaster=t,
        )
    for f in frames:
        assert f["event"] == "add_drawer", f"Expected add_drawer, got {f['event']}"


def test_ops_filter_allows_multiple_operations():
    """AC-010: ?ops=add_drawer,delete_drawer passes both operations."""
    events_to_send = [
        _make_broadcast_event(1, "add_drawer"),
        _make_broadcast_event(2, "update_drawer"),
        _make_broadcast_event(3, "delete_drawer"),
    ]
    with _live_server() as base_url:
        t = _broadcast_after_delay(events_to_send, delay=0.1)
        frames = _collect_sse_frames(
            base_url, n_frames=2,
            params={"ops": "add_drawer,delete_drawer"},
            broadcaster=t,
        )
    event_types = {f["event"] for f in frames}
    assert "update_drawer" not in event_types
    assert event_types.issubset({"add_drawer", "delete_drawer"})


def test_overrun_frame_emitted_on_queue_overflow():
    """AC-015/AC-020: Overrun synthetic frame emitted when queue drops oldest entry on overflow."""
    q = ev.subscribe()
    # Fill queue to capacity
    for i in range(ev._QUEUE_MAXSIZE):
        q.put_nowait({"id": i, "event": "add_drawer", "data": {}})
    assert q.full()

    # Trigger overflow via broadcast
    ev.broadcast({"id": ev._QUEUE_MAXSIZE + 1, "event": "add_drawer", "data": {}})

    # Drain and find overrun frame
    items = []
    while not q.empty():
        items.append(q.get_nowait())

    overrun_frames = [x for x in items if x.get("event") == "overrun"]
    assert len(overrun_frames) >= 1, "Expected at least one overrun frame"
    ev.unsubscribe(q)


# ---------------------------------------------------------------------------
# Story p1 / AC-P06: Truncation-recovery regression test
# ---------------------------------------------------------------------------


async def _truncation_recovery_regression():
    q = ev.subscribe()
    await ev.start_tailer()

    # Write 3 entries and wait for all 3 to be broadcast
    for i in range(3):
        _write_wal_entry(_make_entry("add_drawer", drawer_id=f"pre{i}"))

    received_pre = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(received_pre) < 3:
        await asyncio.sleep(0.05)
        while not q.empty():
            received_pre.append(q.get_nowait())

    assert len(received_pre) == 3, f"Expected 3 pre-truncation events, got {len(received_pre)}"
    pre_trunc_id = received_pre[-1]["id"]

    # Truncate WAL to 0 bytes
    wal_path = str(_mp._WAL_FILE)
    with open(wal_path, "w") as fh:
        fh.write("")

    # Allow tailer to detect truncation
    await asyncio.sleep(0.3)

    # Write 2 fresh entries after truncation
    for i in range(2):
        _write_wal_entry(_make_entry("update_drawer", drawer_id=f"post{i}"))

    received_post = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(received_post) < 2:
        await asyncio.sleep(0.05)
        while not q.empty():
            received_post.append(q.get_nowait())

    await ev.stop_tailer()
    ev.unsubscribe(q)
    return pre_trunc_id, received_post


def test_truncation_recovery_ids_strictly_increasing():
    """AC-P06: Post-truncation events have ids strictly greater than pre-truncation cursor."""
    pre_trunc_id, received_post = _run(_truncation_recovery_regression())
    assert len(received_post) == 2, f"Expected 2 post-truncation events, got {len(received_post)}"
    for evt in received_post:
        assert evt["id"] > pre_trunc_id, (
            f"Post-truncation event id {evt['id']} not > pre_trunc_id {pre_trunc_id}"
        )


def test_truncation_recovery_replay_from_id_non_empty():
    """AC-P06: replay_from_id with pre-truncation cursor returns cursor-stale or post-truncation events."""
    pre_trunc_id, received_post = _run(_truncation_recovery_regression())
    # Ring buffer has at least the 2 post-truncation events now
    replay = ev.replay_from_id(pre_trunc_id)
    assert len(replay) > 0, "replay_from_id returned empty for pre-truncation cursor"
    for evt in replay:
        assert evt["id"] > pre_trunc_id, (
            f"Replayed event id {evt['id']} not > pre_trunc_id {pre_trunc_id}"
        )
