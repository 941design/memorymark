import asyncio
import hashlib
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
from mempalace.config import sanitize_content, sanitize_name
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
    tool_list_rooms,
    tool_list_wings,
    tool_status,
)
from mempalace.searcher import build_where_filter

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
    result = tool_status()
    count = result.get("total_drawers", 0)
    return {"status": "ok", "drawers": count}


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
            return {"success": True, "drawer_id": drawer_id}
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

    return {"success": True, "drawer_id": drawer_id}


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

    return {"success": True, "drawer_id": drawer_id}


@app.delete("/api/drawers/{drawer_id}")
def delete_drawer(drawer_id: str):
    result = tool_delete_drawer(drawer_id=drawer_id)
    if not result.get("success"):
        error = result.get("error", "Unknown error")
        if _is_not_found(result):
            raise HTTPException(status_code=404, detail=error)
        raise HTTPException(status_code=400, detail=error)
    return {"success": True}


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
):
    limit = max(1, min(limit, 50))
    col = _get_collection()
    if col is None:
        return {
            "query": query,
            "filters": {"wing": wing, "room": room},
            "results": [],
        }

    try:
        wing_filter = sanitize_name(wing, "wing") if wing else None
        room_filter = sanitize_name(room, "room") if room else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    where = build_where_filter(wing_filter, room_filter)
    query_kwargs = {
        "query_texts": [query],
        "n_results": limit,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    try:
        chroma_results = col.query(**query_kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {e}")

    ids = chroma_results["ids"][0] if chroma_results.get("ids") else []
    docs = chroma_results["documents"][0] if chroma_results.get("documents") else []
    metas = chroma_results["metadatas"][0] if chroma_results.get("metadatas") else []
    dists = chroma_results["distances"][0] if chroma_results.get("distances") else []

    hits = []
    for drawer_id, doc, meta, dist in zip(ids, docs, metas, dists):
        if max_distance is not None and dist > max_distance:
            continue
        meta = meta or {}
        source_url = meta.get("source_url") or meta.get("source_file", "")
        hits.append({
            "drawer_id": drawer_id,
            "content": doc or "",
            "metadata": {
                "source_url": source_url,
                "title": meta.get("title", ""),
                "timestamp": meta.get("timestamp") or meta.get("filed_at", ""),
            },
            "distance": round(dist, 4),
            "wing": meta.get("wing", ""),
            "room": meta.get("room", ""),
            "added_by": meta.get("added_by", ""),
        })

    return {
        "query": query,
        "filters": {"wing": wing_filter, "room": room_filter},
        "results": hits,
    }


@app.get("/api/status")
def status():
    result = tool_status()
    if "error" in result and "total_drawers" not in result:
        return {"total_drawers": 0, "wings": {}, "rooms": {}}
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


@app.get("/api/drawers/by-url")
def drawers_by_url(url: str = Query(...), include_content: bool = Query(False)):
    col = _get_collection()
    if col is None:
        return {"exists": False}
    includes = ["metadatas"]
    if include_content:
        includes.append("documents")
    results = col.get(where={"source_url": url}, include=includes)
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
    return result
