"""WAL tailer, subscriber registry, ring buffer, and broadcaster for SSE events."""

import asyncio
import json
import logging
from collections import deque
from typing import Any

import mempalace.mcp_server as _mp

logger = logging.getLogger("memorymark.events")

_POLL_INTERVAL = 0.1  # seconds
_QUEUE_MAXSIZE = 256
_RING_MAXSIZE = 1024

_subscribers: set[asyncio.Queue] = set()
_ring_buffer: deque[dict[str, Any]] = deque(maxlen=_RING_MAXSIZE)

_tailer_task: asyncio.Task | None = None
_event_loop: asyncio.AbstractEventLoop | None = None
_global_seq: int = 0


def subscribe() -> asyncio.Queue:
    """Create a new subscriber queue and register it."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """Remove a subscriber queue from the registry."""
    _subscribers.discard(q)


def _put_to_queue(q: asyncio.Queue, item: dict[str, Any]) -> None:
    """Put item into queue. When full, drop two oldest, then insert overrun frame and item."""
    if q.full():
        for _ in range(2):
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break
        overrun: dict[str, Any] = {"event": "overrun", "data": {}}
        q.put_nowait(overrun)
    q.put_nowait(item)


def broadcast(event: dict[str, Any]) -> None:
    """Push event to ring buffer and all subscriber queues.

    Thread-safe: uses call_soon_threadsafe when called from outside the event loop.
    """
    _ring_buffer.append(event)
    loop = _event_loop
    for q in list(_subscribers):
        if loop is not None and loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is not loop:
                # Called from a different thread — schedule on the event loop
                loop.call_soon_threadsafe(_put_to_queue, q, event)
                continue
        _put_to_queue(q, event)


async def _wal_tailer() -> None:
    """Tail the WAL file and broadcast new entries to subscribers."""
    global _global_seq
    line_number = 0
    file_pos = 0

    while True:
        try:
            current_wal_path = str(_mp._WAL_FILE)
            try:
                file_size = await asyncio.to_thread(_get_file_size, current_wal_path)
            except OSError:
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            if file_size < file_pos:
                # Truncation or rotation detected — reset file-offset tracking only
                logger.warning("WAL truncated; resetting tailer to offset 0")
                file_pos = 0
                line_number = 0
                # _global_seq intentionally not reset

            if file_size > file_pos:
                new_lines = await asyncio.to_thread(
                    _read_lines_from, current_wal_path, file_pos
                )
                for raw, nbytes in new_lines:
                    file_pos += nbytes
                    line_number += 1
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    _global_seq += 1
                    event = {
                        "id": _global_seq,
                        "event": entry.get("operation", "unknown"),
                        "data": {
                            "timestamp": entry.get("timestamp", ""),
                            "operation": entry.get("operation", ""),
                            "params": entry.get("params", {}),
                        },
                    }
                    broadcast(event)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WAL tailer error")

        await asyncio.sleep(_POLL_INTERVAL)


def _get_file_size(path: str) -> int:
    """Return the size of a file in bytes, or 0 if it does not exist."""
    import os
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _read_lines_from(path: str, offset: int) -> list[tuple[str, int]]:
    """Read new lines from *path* starting at byte *offset*.

    Returns a list of (line_text, byte_length) pairs.
    """
    results: list[tuple[str, int]] = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        for raw_bytes in fh:
            results.append((raw_bytes.decode("utf-8", errors="replace").rstrip("\n"), len(raw_bytes)))
    return results


def replay_from_id(last_id: int) -> list[dict[str, Any]]:
    """Return events with id > last_id from the ring buffer.

    If the ring buffer is empty, returns [].
    If last_id predates the ring buffer's oldest entry, returns a synthetic
    cursor-stale frame so the client knows to reset its cursor.
    """
    buf = list(_ring_buffer)
    if not buf:
        return []
    oldest_id = buf[0].get("id", 0)
    if last_id < oldest_id - 1:
        # Cursor is older than ring buffer can serve — emit stale signal
        return [{"id": oldest_id - 1, "event": "cursor-stale", "data": {"min_id": oldest_id}}]
    return [e for e in buf if e.get("id", 0) > last_id]


def replay_from_timestamp(since_ts: str) -> list[dict[str, Any]]:
    """Return events whose timestamp >= since_ts (lexicographic ISO-8601 comparison).

    Scans the ring buffer first; if any ring-buffer event has timestamp < since_ts,
    falls back to a single WAL scan from the beginning to guarantee completeness.
    No second scan is performed.
    """
    buf = list(_ring_buffer)
    if buf and buf[0].get("data", {}).get("timestamp", "") >= since_ts:
        # Entire ring buffer is within range — serve from buffer only
        return [e for e in buf if e.get("data", {}).get("timestamp", "") >= since_ts]
    # Need WAL scan — may include events older than ring buffer
    return _scan_wal_by_timestamp(str(_mp._WAL_FILE), since_ts)



def _scan_wal_by_timestamp(path: str, since_ts: str) -> list[dict[str, Any]]:
    """Single sequential WAL scan returning events with timestamp >= since_ts."""
    results: list[dict[str, Any]] = []
    try:
        with open(path, "rb") as fh:
            line_number = 0
            for raw_bytes in fh:
                line_number += 1
                raw = raw_bytes.decode("utf-8", errors="replace").rstrip("\n")
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")
                if ts >= since_ts:
                    results.append({
                        "id": line_number,
                        "event": entry.get("operation", "unknown"),
                        "data": {
                            "timestamp": ts,
                            "operation": entry.get("operation", ""),
                            "params": entry.get("params", {}),
                        },
                    })
    except OSError:
        pass
    return results


async def start_tailer() -> None:
    """Start the WAL tailer background task."""
    global _tailer_task, _event_loop
    _event_loop = asyncio.get_running_loop()
    _tailer_task = asyncio.create_task(_wal_tailer())


async def stop_tailer() -> None:
    """Cancel and await the WAL tailer background task."""
    global _tailer_task, _event_loop
    if _tailer_task is not None:
        _tailer_task.cancel()
        try:
            await _tailer_task
        except asyncio.CancelledError:
            pass
        _tailer_task = None
    _event_loop = None
