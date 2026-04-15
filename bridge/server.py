import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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
    tool_get_taxonomy,
    tool_list_rooms,
    tool_list_wings,
    tool_search,
    tool_status,
)

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
    async with _mcp_session_manager.run():
        yield
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
        "added_by": "memorymark",
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
            "added_by": "memorymark",
            "content_length": len(content),
            "content_preview": content[:200],
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
            "content_preview": new_doc[:200] if req.content is not None else None,
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
    kwargs = {"query": query, "limit": limit}
    if wing is not None:
        kwargs["wing"] = wing
    if room is not None:
        kwargs["room"] = room
    if max_distance is not None:
        kwargs["max_distance"] = max_distance
    return tool_search(**kwargs)


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
    meta = result["metadatas"][0]
    return {
        "drawer_id": drawer_id,
        "content": result["documents"][0] if result.get("documents") else "",
        "wing": meta.get("wing", ""),
        "room": meta.get("room", ""),
        "title": meta.get("title", ""),
        "source_url": meta.get("source_url", ""),
    }
