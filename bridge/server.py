import asyncio
import functools
import hashlib
import os
import threading
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("memorymark.bridge")

from mcp.server import Server as MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, TextContent, Tool

import mempalace.mcp_server as _mp
from mempalace.config import sanitize_content as _mp_sanitize_content, sanitize_name
from mempalace.mcp_server import (
    TOOLS as MEMPALACE_TOOLS,
    _get_collection,
    handle_request as _mempalace_handle_request,
    tool_delete_drawer,
    tool_diary_write,
    tool_get_taxonomy,
    tool_kg_add,
    tool_kg_invalidate,
    tool_kg_query,
    tool_list_drawers,
    tool_list_rooms,
    tool_list_wings,
    tool_status,
)
from mempalace.searcher import build_where_filter

import passages

# Drawer/diary content cap. MemPalace defaults to 100_000 characters; the
# bridge doubles it. The override is also installed into mempalace.mcp_server
# (which imported sanitize_content by name) so MCP writes at /mcp enforce the
# same cap as /api/*. Keep extension/content.js MAX_LENGTH in sync.
MAX_CONTENT_LENGTH = 200_000


def sanitize_content(value: str) -> str:
    return _mp_sanitize_content(value, max_length=MAX_CONTENT_LENGTH)


_mp.sanitize_content = sanitize_content

# ---------------------------------------------------------------------------
# MCP Streamable HTTP: expose MemPalace's full tool surface at /mcp so agent
# harnesses (Claude Desktop, Claude Code, Codex, Cursor, Zed, …) share the
# same palace as the browser extension (which uses /api/*).
# ---------------------------------------------------------------------------

mcp_server: MCPServer = MCPServer("memorymark")


@mcp_server.list_tools()
async def _mcp_list_tools() -> list[Tool]:
    return [
        Tool(name=name, description=tool["description"], inputSchema=tool["input_schema"])
        for name, tool in MEMPALACE_TOOLS.items()
    ]


@mcp_server.call_tool(validate_input=False)
async def _mcp_call_tool(name: str, arguments: dict) -> list[TextContent]:
    # Delegate to MemPalace's handle_request so argument whitelisting, type
    # coercion, and error semantics stay identical to the stdio MCP server.
    response = await asyncio.to_thread(
        _mempalace_handle_request,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    if response is None:
        raise McpError(ErrorData(code=-32603, message="Empty response from MemPalace"))
    if "error" in response:
        err = response["error"]
        raise McpError(
            ErrorData(code=err.get("code", -32603), message=err.get("message", "Tool error"))
        )
    content = response.get("result", {}).get("content", [])
    return [TextContent(type="text", text=item.get("text", "")) for item in content]


# ---------------------------------------------------------------------------
# Passage index (see passages.py, specs/passage-index-spec.md)
# ---------------------------------------------------------------------------


def _index() -> passages.PassageIndex:
    return passages.get_index(_mp._config.palace_path, lambda: _get_collection())


def _sync_index(drawer_id: str | None) -> bool:
    """Sync one drawer's passages; never raises (the article is already stored).

    Returns False on failure; the next reconcile repairs it.
    """
    if not drawer_id:
        return False
    try:
        _index().sync(drawer_id)
        return True
    except Exception:
        logger.exception("passage index sync failed for %s", drawer_id)
        return False


def _wrap_write_tool(tool_name: str, id_from) -> None:
    """Sync the passage index after a successful MemPalace MCP write.

    functools.wraps keeps the original signature visible to inspect.signature,
    so handle_request still whitelists arguments against the tool schema
    (a bare **kwargs wrapper would disable that and allow spoofing added_by).
    """
    original = MEMPALACE_TOOLS[tool_name]["handler"]
    if getattr(original, "_memorymark_wrapped", False):
        return

    @functools.wraps(original)
    def handler(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(result, dict) and result.get("success"):
            _sync_index(id_from(result, kwargs))
        return result

    handler._memorymark_wrapped = True
    MEMPALACE_TOOLS[tool_name]["handler"] = handler


_wrap_write_tool("mempalace_add_drawer", lambda r, kw: r.get("drawer_id"))
_wrap_write_tool("mempalace_update_drawer", lambda r, kw: r.get("drawer_id") or kw.get("drawer_id"))
_wrap_write_tool("mempalace_delete_drawer", lambda r, kw: r.get("drawer_id") or kw.get("drawer_id"))
_wrap_write_tool("mempalace_diary_write", lambda r, kw: r.get("entry_id"))


def _article_search(query: str, where: dict | None, limit: int, max_distance: float | None) -> list[dict]:
    """Legacy article-level search; used while the passage index is empty."""
    col = _get_collection()
    if col is None:
        return []
    kwargs = {"query_texts": [query], "n_results": limit, "include": ["documents", "metadatas", "distances"]}
    if where:
        kwargs["where"] = where
    res = col.query(**kwargs)
    hits = []
    ids = res["ids"][0] if res.get("ids") else []
    for did, doc, meta, dist in zip(ids, res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if max_distance is not None and dist > max_distance:
            continue
        doc = doc or ""
        hits.append({
            "drawer_id": did, "document": doc, "metadata": meta or {}, "distance": dist,
            "passage_start": 0, "passage_end": min(len(doc), 1000), "matched_passages": 1,
        })
    return hits


def grouped_search(query: str, wing: str | None, room: str | None, limit: int,
                   max_distance: float | None) -> tuple[list[dict], str]:
    """One hit per article. Returns (hits, index_used: "passages" | "articles")."""
    where = build_where_filter(wing, room)
    idx = _index()
    if idx.passage_count() == 0:
        return _article_search(query, where, limit, max_distance), "articles"
    return idx.search(query, where=where or None, limit=limit, max_distance=max_distance), "passages"


def _mcp_search(query: str, limit: int = 5, wing: str = None, room: str = None,
                max_distance: float = 1.5, min_similarity: float = None, context: str = None):
    """MemPalace's mempalace_search, answered from the passage index.

    Keeps MemPalace's result keys (text, wing, room, source_file, similarity,
    distance); ``text`` is the best-matching passage. Adds drawer_id, title,
    source_url, matched_passages.
    """
    limit = max(1, min(limit, _mp._MAX_RESULTS))
    try:
        wing = _mp._sanitize_optional_name(wing, "wing")
        room = _mp._sanitize_optional_name(room, "room")
    except ValueError as e:
        return {"error": str(e)}
    dist = (1.0 - min_similarity) if min_similarity is not None else max_distance
    sanitized = _mp.sanitize_query(query)
    try:
        hits, used = grouped_search(
            sanitized["clean_query"], wing, room, limit, dist if dist and dist > 0 else None
        )
    except Exception as e:
        logger.exception("mcp search failed")
        return {"error": f"Search error: {e}"}
    results = []
    for h in hits:
        meta = h["metadata"]
        source = meta.get("source_url") or meta.get("source_file", "")
        results.append({
            "drawer_id": h["drawer_id"],
            "text": h["document"][h["passage_start"]:h["passage_end"]],
            "wing": meta.get("wing", "unknown"),
            "room": meta.get("room", "unknown"),
            "source_file": os.path.basename(meta.get("source_file", "") or "") or "?",
            "source_url": source,
            "title": meta.get("title", ""),
            "matched_passages": h["matched_passages"],
            "similarity": round(max(0.0, 1 - h["distance"]), 3),
            "distance": round(h["distance"], 4),
        })
    result = {"query": query, "filters": {"wing": wing, "room": room}, "index": used, "results": results}
    if sanitized["was_sanitized"]:
        result["query_sanitized"] = True
        result["sanitizer"] = {
            "method": sanitized["method"],
            "original_length": sanitized["original_length"],
            "clean_length": sanitized["clean_length"],
            "clean_query": sanitized["clean_query"],
        }
    if context:
        result["context_received"] = True
    return result


# Same signature as MemPalace's tool_search, so handle_request's argument
# whitelisting is unaffected; functools.update_wrapper keeps our docstring.
functools.update_wrapper(_mcp_search, _mp.tool_search, assigned=("__module__", "__name__", "__qualname__"))
MEMPALACE_TOOLS["mempalace_search"]["handler"] = _mcp_search
MEMPALACE_TOOLS["mempalace_search"]["description"] = (
    "Semantic search over every passage of every drawer. Returns one hit per drawer: "
    "'text' is the best-matching passage, 'drawer_id' identifies the drawer — use "
    "mempalace_get_drawer for its full content. IMPORTANT: 'query' must contain ONLY "
    "search keywords. Use 'context' for background. Results with cosine distance > "
    "max_distance are filtered out."
)


def _start_reconcile_thread() -> threading.Thread | None:
    if os.environ.get("MEMORYMARK_RECONCILE_ON_START", "1") == "0":
        return None

    def run():
        try:
            if _get_collection() is None:
                return
            rs = _index().reconcile(full=False)
            logger.info("passage index reconcile: %s", rs.as_dict())
        except Exception:
            logger.exception("start-up passage reconcile failed")

    t = threading.Thread(target=run, name="passage-reconcile", daemon=True)
    t.start()
    return t


# StreamableHTTPSessionManager can only be run() once per instance, so we
# rebuild it each time the FastAPI lifespan enters (important for tests that
# spin up TestClient repeatedly).
_mcp_session_manager: StreamableHTTPSessionManager | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _mcp_session_manager
    _mcp_session_manager = StreamableHTTPSessionManager(
        app=mcp_server, stateless=True, json_response=True
    )
    from events import start_tailer, stop_tailer
    async with _mcp_session_manager.run():
        _start_reconcile_thread()
        await start_tailer()
        yield
        await stop_tailer()
    _mcp_session_manager = None


async def _mcp_asgi(scope, receive, send):
    if _mcp_session_manager is None:
        raise RuntimeError("MCP session manager not initialized; lifespan did not run")
    await _mcp_session_manager.handle_request(scope, receive, send)


app = FastAPI(title="MemoryMark Bridge", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension|moz-extension)://.*$",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"],
)

app.mount("/mcp", _mcp_asgi)

def _is_not_found(result: dict) -> bool:
    """Check if a MemPalace result indicates the resource was not found."""
    if result.get("success"):
        return False
    error = result.get("error", "")
    return "not found" in error.lower() or "no palace" in error.lower()


# ---------------------------------------------------------------------------
# S1: Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health():
    # col.count() only -- tool_status() also walks every drawer's metadata to
    # build wing/room histograms, which takes seconds on a large palace and
    # trips liveness probes (e.g. `make install`'s 2s curl timeout).
    col = _get_collection()
    return {"status": "ok", "drawers": col.count() if col else 0}


# ---------------------------------------------------------------------------
# S2: Pydantic models + Write endpoints
# ---------------------------------------------------------------------------


class DrawerMetadata(BaseModel):
    source_url: str | None = None
    title: str | None = None
    timestamp: str | None = None


class CreateDrawerRequest(BaseModel):
    wing: str
    room: str
    content: str
    added_by: str | None = None
    metadata: DrawerMetadata | None = None


class UpdateDrawerRequest(BaseModel):
    content: str | None = None
    wing: str | None = None
    room: str | None = None
    metadata: DrawerMetadata | None = None


@app.post("/api/drawers", status_code=201)
def create_drawer(req: CreateDrawerRequest):
    try:
        wing = sanitize_name(req.wing, "wing")
        room = sanitize_name(req.room, "room")
        content = sanitize_content(req.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    col = _get_collection(create=True)
    if col is None:
        raise HTTPException(status_code=500, detail="No palace found")

    source_url = req.metadata.source_url if req.metadata else None
    added_by = req.added_by or "memorymark"
    drawer_id = (
        f"drawer_{wing}_{room}_"
        f"{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
    )

    # Idempotency: if this exact drawer already exists, return success as a no-op.
    try:
        existing = col.get(ids=[drawer_id])
        if existing and existing["ids"]:
            return {"success": True, "drawer_id": drawer_id, "indexed": _sync_index(drawer_id)}
    except Exception:
        pass

    meta = {
        "wing": wing,
        "room": room,
        "source_file": source_url or "",
        "chunk_index": 0,
        "added_by": added_by,
        "filed_at": datetime.now().isoformat(),
    }
    if req.metadata:
        if req.metadata.source_url is not None:
            meta["source_url"] = req.metadata.source_url
        if req.metadata.title is not None:
            meta["title"] = req.metadata.title
        if req.metadata.timestamp is not None:
            meta["timestamp"] = req.metadata.timestamp

    _mp._wal_log(
        "add_drawer",
        {
            "drawer_id": drawer_id,
            "wing": wing,
            "room": room,
            "added_by": added_by,
            "content_length": len(content),
            "content_preview": content[:200],
            "source_url": source_url or "",
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        },
    )

    try:
        col.upsert(ids=[drawer_id], documents=[content], metadatas=[meta])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _mp._metadata_cache = None

    return {"success": True, "drawer_id": drawer_id, "indexed": _sync_index(drawer_id)}


@app.put("/api/drawers/{drawer_id}")
def update_drawer(drawer_id: str, req: UpdateDrawerRequest):
    if (
        req.content is None
        and req.wing is None
        and req.room is None
        and req.metadata is None
    ):
        return {"success": True, "drawer_id": drawer_id, "noop": True}

    col = _get_collection()
    if col is None:
        raise HTTPException(status_code=404, detail=f"Drawer not found: {drawer_id}")

    try:
        existing = col.get(ids=[drawer_id], include=["documents", "metadatas"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not existing["ids"]:
        raise HTTPException(status_code=404, detail=f"Drawer not found: {drawer_id}")

    old_meta = existing["metadatas"][0]
    old_doc = existing["documents"][0] if existing.get("documents") else ""

    new_doc = old_doc
    if req.content is not None:
        try:
            new_doc = sanitize_content(req.content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    new_meta = dict(old_meta)
    if req.wing is not None:
        try:
            new_meta["wing"] = sanitize_name(req.wing, "wing")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if req.room is not None:
        try:
            new_meta["room"] = sanitize_name(req.room, "room")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if req.metadata:
        if req.metadata.source_url is not None:
            new_meta["source_url"] = req.metadata.source_url
            new_meta["source_file"] = req.metadata.source_url
        if req.metadata.title is not None:
            new_meta["title"] = req.metadata.title
        if req.metadata.timestamp is not None:
            new_meta["timestamp"] = req.metadata.timestamp

    _mp._wal_log(
        "update_drawer",
        {
            "drawer_id": drawer_id,
            "old_wing": old_meta.get("wing", ""),
            "old_room": old_meta.get("room", ""),
            "new_wing": new_meta.get("wing", ""),
            "new_room": new_meta.get("room", ""),
            "content_changed": req.content is not None,
            "content_preview": new_doc[:200],
            "added_by": old_meta.get("added_by", ""),
            "source_url": new_meta.get("source_url", old_meta.get("source_url", old_meta.get("source_file", ""))),
            "content_hash": hashlib.sha256(new_doc.encode()).hexdigest(),
        },
    )

    update_kwargs = {"ids": [drawer_id], "metadatas": [new_meta]}
    if req.content is not None:
        update_kwargs["documents"] = [new_doc]
    try:
        col.update(**update_kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _mp._metadata_cache = None

    return {"success": True, "drawer_id": drawer_id, "indexed": _sync_index(drawer_id)}


@app.delete("/api/drawers/{drawer_id}")
def delete_drawer(drawer_id: str):
    result = tool_delete_drawer(drawer_id=drawer_id)
    if not result.get("success"):
        error = result.get("error", "Unknown error")
        if _is_not_found(result):
            raise HTTPException(status_code=404, detail=error)
        raise HTTPException(status_code=400, detail=error)
    # tool_delete_drawer is called directly (not via the MCP TOOLS wrapper).
    return {"success": True, "indexed": _sync_index(drawer_id)}


# ---------------------------------------------------------------------------
# S3: Read endpoints
# ---------------------------------------------------------------------------


@app.get("/api/search")
def search(
    query: str = Query(...),
    wing: str | None = Query(None),
    room: str | None = Query(None),
    max_distance: float | None = Query(None),
    limit: int = Query(5),
    include_content: bool = Query(True),
):
    """One hit per article, ranked by its best-matching passage."""
    limit = max(1, min(limit, 50))
    try:
        wing_filter = sanitize_name(wing, "wing") if wing else None
        room_filter = sanitize_name(room, "room") if room else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    empty = {"query": query, "filters": {"wing": wing_filter, "room": room_filter}, "results": [], "index": "articles"}
    if _get_collection() is None:
        return empty

    try:
        hits, used = grouped_search(query, wing_filter, room_filter, limit, max_distance)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {e}")

    results = []
    for h in hits:
        meta = h["metadata"] or {}
        doc = h["document"]
        item = {
            "drawer_id": h["drawer_id"],
            "passage": doc[h["passage_start"]:h["passage_end"]],
            "passage_start": h["passage_start"],
            "passage_end": h["passage_end"],
            "matched_passages": h["matched_passages"],
            "metadata": {
                "source_url": meta.get("source_url") or meta.get("source_file", ""),
                "title": meta.get("title", ""),
                "timestamp": meta.get("timestamp") or meta.get("filed_at", ""),
            },
            "distance": round(h["distance"], 4),
            "wing": meta.get("wing", ""),
            "room": meta.get("room", ""),
            "added_by": meta.get("added_by", ""),
        }
        if include_content:
            item["content"] = doc
        results.append(item)
    return {**empty, "results": results, "index": used}


@app.get("/api/status")
def status():
    result = tool_status()
    if "error" in result and "total_drawers" not in result:
        return {"total_drawers": 0, "wings": {}, "rooms": {}}
    try:
        stats = _index().stats()
        total = result.get("total_drawers", 0)
        stats["coverage"] = min(1.0, round(stats["indexed_articles"] / total, 4)) if total else 1.0
        result["passage_index"] = stats
    except Exception as e:
        result["passage_index"] = {"error": str(e)}
    return result


@app.get("/api/taxonomy")
def taxonomy():
    result = tool_get_taxonomy()
    if "error" in result and "taxonomy" not in result:
        return {"taxonomy": {}}
    return result


@app.get("/api/wings")
def wings():
    result = tool_list_wings()
    if "error" in result and "wings" not in result:
        return {"wings": {}}
    return result


@app.get("/api/rooms")
def rooms(wing: str | None = Query(None)):
    result = tool_list_rooms(wing=wing)
    if "error" in result and "rooms" not in result:
        return {"wing": wing or "all", "rooms": {}}
    return result


@app.get("/api/drawers")
def list_drawers(
    wing: str | None = Query(None),
    room: str | None = Query(None),
    limit: int = Query(20),
    offset: int = Query(0),
):
    result = tool_list_drawers(wing=wing, room=room, limit=limit, offset=offset)
    if "error" in result:
        if result["error"].startswith("No palace found"):
            return {"drawers": [], "count": 0, "offset": offset, "limit": limit}
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/drawers/by-url")
def drawers_by_url(url: str = Query(...), include_content: bool = Query(False)):
    col = _get_collection()
    if col is None:
        return {"exists": False}
    includes = ["metadatas"]
    if include_content:
        includes.append("documents")
    # Drawers created via the bridge store provenance in source_url; drawers
    # created via MemPalace's native MCP add_drawer tool only set source_file.
    # Match either so the unified-palace contract holds across writers.
    results = col.get(
        where={"$or": [{"source_url": url}, {"source_file": url}]},
        include=includes,
    )
    if results and results["ids"]:
        meta = results["metadatas"][0]
        resp = {
            "exists": True,
            "drawer_id": results["ids"][0],
            "title": meta.get("title", ""),
        }
        if include_content:
            resp["content"] = results["documents"][0] if results.get("documents") else ""
        return resp
    return {"exists": False}


@app.get("/api/drawers/{drawer_id}")
def get_drawer(drawer_id: str):
    col = _get_collection()
    if col is None:
        raise HTTPException(status_code=404, detail=f"Drawer not found: {drawer_id}")
    result = col.get(ids=[drawer_id], include=["documents", "metadatas"])
    if not result["ids"]:
        raise HTTPException(status_code=404, detail=f"Drawer not found: {drawer_id}")
    meta = result["metadatas"][0] or {}
    content = result["documents"][0] if result.get("documents") else ""
    source_url = meta.get("source_url") or meta.get("source_file", "")
    return {
        "drawer_id": drawer_id,
        "content": content,
        "wing": meta.get("wing", ""),
        "room": meta.get("room", ""),
        "added_by": meta.get("added_by", ""),
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": {
            "source_url": source_url,
            "title": meta.get("title", ""),
            "timestamp": meta.get("timestamp") or meta.get("filed_at", ""),
        },
    }


# ---------------------------------------------------------------------------
# S4: SSE event stream
# ---------------------------------------------------------------------------

_SSE_HEARTBEAT_INTERVAL: float = 3.0  # seconds; monkeypatch in tests. Must be < httpx default read timeout (5 s).
_SSE_POLL_TIMEOUT: float = 1.0  # inner loop poll; keeps disconnect detection responsive
_SSE_CONTROL_EVENTS: frozenset[str] = frozenset({"cursor-stale", "overrun"})


@app.get("/api/events")
async def get_events(
    request: Request,
    since_timestamp: str | None = Query(None),
    ops: str | None = Query(None),
) -> StreamingResponse:
    from events import subscribe, unsubscribe, replay_from_id, replay_from_timestamp

    last_event_id_header = request.headers.get("last-event-id")
    ops_filter: set[str] | None = None
    if ops:
        ops_filter = {o.strip() for o in ops.split(",") if o.strip()}

    def _matches_ops(event: dict) -> bool:
        event_name = event.get("event", "")
        if event_name in _SSE_CONTROL_EVENTS:
            return True
        if ops_filter is None:
            return True
        return event_name in ops_filter

    def _format_event(event: dict) -> str:
        event_id = event.get("id", "")
        return (
            f"id: {event_id}\n"
            f"event: {event['event']}\n"
            f"data: {json.dumps(event.get('data', {}))}\n\n"
        )

    async def _generate() -> AsyncGenerator[str, None]:
        # Replay phase
        if last_event_id_header is not None:
            try:
                last_id = int(last_event_id_header)
            except (ValueError, TypeError):
                last_id = 0
            replay_events = await asyncio.to_thread(replay_from_id, last_id)
            for ev in replay_events:
                if _matches_ops(ev):
                    yield _format_event(ev)
        elif since_timestamp is not None:
            replay_events = await asyncio.to_thread(replay_from_timestamp, since_timestamp)
            for ev in replay_events:
                if _matches_ops(ev):
                    yield _format_event(ev)

        # Live streaming phase
        q = subscribe()
        idle_seconds = 0.0
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_SSE_POLL_TIMEOUT)
                except asyncio.TimeoutError:
                    idle_seconds += _SSE_POLL_TIMEOUT
                    if idle_seconds >= _SSE_HEARTBEAT_INTERVAL:
                        yield ": keepalive\n\n"
                        idle_seconds = 0.0
                    continue
                idle_seconds = 0.0
                if _matches_ops(event):
                    yield _format_event(event)
        finally:
            unsubscribe(q)

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# S5: Knowledge Graph endpoints
# ---------------------------------------------------------------------------

# Mirrors mempalace.config.sanitize_name so KG validation errors can surface
# an actionable allowed_pattern to clients. Keep in sync with docs/rest-api.md
# and the upstream rule if mempalace ever loosens or tightens it.
_KG_ALLOWED_PATTERN = r"^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$"
_KG_ALLOWED_CHAR_RE = re.compile(r"[\w .'-]")
_KG_MAX_LENGTH = 128
_KG_ALLOWED_DESCRIPTION = (
    "Unicode alphanumerics, underscore, space, dot, apostrophe, and hyphen. "
    "First and last character must be alphanumeric. "
    f"Max {_KG_MAX_LENGTH} characters."
)


def _kg_validation_response(error: str, submitted: dict[str, str]) -> JSONResponse:
    """Enrich a sanitize_name 400 with field/value/allowed_pattern.

    `submitted` maps field name -> raw submitted value so the caller can see
    which value was rejected without parsing the error string. Errors not
    matching any known pattern fall through with just `detail`.
    """
    body: dict[str, object] = {"detail": error}
    for field_name, value in submitted.items():
        if not error.startswith(f"{field_name} "):
            continue
        body["field"] = field_name
        body["value"] = value
        if "contains invalid characters" in error:
            body["allowed_pattern"] = _KG_ALLOWED_PATTERN
            body["allowed_description"] = _KG_ALLOWED_DESCRIPTION
            disallowed = sorted({c for c in value if not _KG_ALLOWED_CHAR_RE.match(c)})
            if disallowed:
                body["invalid_chars"] = disallowed
        elif "exceeds maximum length" in error:
            body["max_length"] = _KG_MAX_LENGTH
        break
    return JSONResponse(status_code=400, content=body)


class KGFactRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    valid_from: str | None = None
    source_closet: str | None = None


class KGInvalidateRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    ended: str | None = None


@app.get("/api/kg/query")
def kg_query(
    entity: str = Query(...),
    as_of: str | None = Query(None),
    direction: str = Query("both"),
):
    kwargs = {"entity": entity, "direction": direction}
    if as_of is not None:
        kwargs["as_of"] = as_of
    return tool_kg_query(**kwargs)


@app.post("/api/kg/facts", status_code=201)
def kg_add_fact(req: KGFactRequest):
    kwargs = {"subject": req.subject, "predicate": req.predicate, "object": req.object}
    if req.valid_from is not None:
        kwargs["valid_from"] = req.valid_from
    if req.source_closet is not None:
        kwargs["source_closet"] = req.source_closet
    result = tool_kg_add(**kwargs)
    if not result.get("success"):
        return _kg_validation_response(
            result.get("error", "Unknown error"),
            {"subject": req.subject, "predicate": req.predicate, "object": req.object},
        )
    return result


@app.post("/api/kg/invalidate")
def kg_invalidate(req: KGInvalidateRequest):
    kwargs = {"subject": req.subject, "predicate": req.predicate, "object": req.object}
    if req.ended is not None:
        kwargs["ended"] = req.ended
    result = tool_kg_invalidate(**kwargs)
    if not result.get("success"):
        return _kg_validation_response(
            result.get("error", "Unknown error"),
            {"subject": req.subject, "predicate": req.predicate, "object": req.object},
        )
    return result


# ---------------------------------------------------------------------------
# S6: Diary endpoint
# ---------------------------------------------------------------------------


class DiaryWriteRequest(BaseModel):
    agent_name: str
    entry: str
    topic: str = "general"


@app.post("/api/diary/write", status_code=201)
def diary_write(req: DiaryWriteRequest):
    result = tool_diary_write(
        agent_name=req.agent_name,
        entry=req.entry,
        topic=req.topic,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    # tool_diary_write is called directly (not via the MCP TOOLS wrapper).
    return {**result, "indexed": _sync_index(result.get("entry_id"))}
