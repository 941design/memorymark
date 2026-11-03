import json


MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def _rpc(client, body):
    resp = client.post("/mcp/", json=body, headers=MCP_HEADERS)
    assert resp.status_code == 200, (resp.status_code, resp.text)
    return resp.json()


def test_initialize_handshake(client):
    data = _rpc(client, INITIALIZE)
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    server_info = data["result"]["serverInfo"]
    assert server_info["name"] == "memorymark"
    assert "tools" in data["result"]["capabilities"]


def test_tools_list_exposes_mempalace_surface(client):
    _rpc(client, INITIALIZE)
    data = _rpc(
        client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    )
    names = {t["name"] for t in data["result"]["tools"]}
    # Sanity: core read/write tools must be exposed.
    assert {
        "mempalace_status",
        "mempalace_search",
        "mempalace_add_drawer",
        "mempalace_list_drawers",
    } <= names
    # Should be ≥ 20 tools (MemPalace exposes 22+).
    assert len(names) >= 20


def test_tools_call_status_returns_drawer_count(client):
    _rpc(client, INITIALIZE)
    # MemPalace creates the palace lazily on first write, so seed one drawer
    # before asking for status.
    create = client.post(
        "/api/drawers",
        json={"wing": "test", "room": "r", "content": "seed content"},
    )
    assert create.status_code == 201

    data = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "mempalace_status", "arguments": {}},
        },
    )
    content = data["result"]["content"]
    assert content and content[0]["type"] == "text"
    payload = json.loads(content[0]["text"])
    assert payload.get("total_drawers") == 1


def test_tools_call_unknown_tool_returns_error(client):
    _rpc(client, INITIALIZE)
    data = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}},
        },
    )
    # Either JSON-RPC-level error or MCP CallToolResult.isError — both acceptable.
    assert "error" in data or data.get("result", {}).get("isError") is True


def test_tools_call_whitelists_unknown_arguments(client):
    """Extra args not in inputSchema must be stripped before reaching handler.

    mempalace_list_drawers's handler takes wing/room/limit/offset — if the
    whitelist is bypassed, passing an unknown kwarg would TypeError.
    """
    _rpc(client, INITIALIZE)
    data = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "mempalace_list_drawers",
                "arguments": {"limit": 1, "arbitrary_unknown_param": "should_be_stripped"},
            },
        },
    )
    # Should succeed — no JSON-RPC error and no isError.
    assert "error" not in data
    assert data.get("result", {}).get("isError") is not True


def test_mcp_and_rest_see_same_palace(client):
    """End-to-end: write a drawer via the REST endpoint the extension uses,
    then read it back via MCP tools/call. This is the core promise of the
    unified-memory daemon."""
    _rpc(client, INITIALIZE)

    create = client.post(
        "/api/drawers",
        json={
            "wing": "Web",
            "room": "test",
            "content": "unified memory sentinel UNIQUEMARK42",
            "metadata": {"source_url": "https://example.com/a", "title": "t"},
        },
    )
    assert create.status_code == 201

    data = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "mempalace_search", "arguments": {"query": "UNIQUEMARK42"}},
        },
    )
    text = data["result"]["content"][0]["text"]
    assert "UNIQUEMARK42" in text


def _add_drawer_via_mcp(client, content):
    _rpc(client, INITIALIZE)
    data = _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "mempalace_add_drawer",
                "arguments": {"wing": "Web", "room": "test", "content": content},
            },
        },
    )
    return data["result"]["content"][0]["text"]


def test_mcp_add_drawer_uses_bridge_content_cap(client):
    """MCP writes enforce the bridge's MAX_CONTENT_LENGTH, not MemPalace's
    100_000 default (server.py overrides mempalace.mcp_server.sanitize_content)."""
    from server import MAX_CONTENT_LENGTH

    ok = json.loads(_add_drawer_via_mcp(client, "y" * MAX_CONTENT_LENGTH))
    assert ok.get("success") is True, ok

    too_big = json.loads(_add_drawer_via_mcp(client, "z" * (MAX_CONTENT_LENGTH + 1)))
    assert too_big.get("success") is not True
    assert "maximum length" in json.dumps(too_big)


def _call(client, name, arguments, rid=20):
    _rpc(client, INITIALIZE)
    data = _rpc(client, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert "error" not in data, data
    return json.loads(data["result"]["content"][0]["text"])


_LONG = "\n\n".join(
    f"Paragraph {i}: ordinary filler text about weather, markets and sports." for i in range(400)
) + "\n\nThe axolotl regenerates its limbs, spinal cord and heart.\n"


def test_mcp_add_and_delete_keep_passage_index_in_sync(client):
    import server
    added = _call(client, "mempalace_add_drawer", {"wing": "Web", "room": "t", "content": _LONG})
    did = added["drawer_id"]
    idx = server._index()
    assert len(idx._col.get(where={"drawer_id": did})["ids"]) > 3
    _call(client, "mempalace_delete_drawer", {"drawer_id": did})
    assert idx._col.get(where={"drawer_id": did})["ids"] == []


def test_mcp_search_is_grouped_and_returns_passages(client):
    added = _call(client, "mempalace_add_drawer", {"wing": "Web", "room": "t", "content": _LONG})
    out = _call(client, "mempalace_search", {"query": "axolotl regenerates limbs", "limit": 3})
    assert out["index"] == "passages"
    ids = [r["drawer_id"] for r in out["results"]]
    assert ids[0] == added["drawer_id"] and len(ids) == len(set(ids))
    top = out["results"][0]
    assert "axolotl" in top["text"] and len(top["text"]) < len(_LONG)
    assert {"wing", "room", "source_file", "similarity", "distance", "matched_passages"} <= top.keys()


def test_mcp_write_wrapper_keeps_argument_whitelist(client):
    """Plan review B2: wrapping handlers must not disable MemPalace's argument
    whitelisting (a **kwargs wrapper would let callers spoof internal params)."""
    import inspect
    import server
    for name in ("mempalace_add_drawer", "mempalace_update_drawer", "mempalace_delete_drawer",
                 "mempalace_diary_write", "mempalace_search"):
        sig = inspect.signature(server.MEMPALACE_TOOLS[name]["handler"])
        assert not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()), name
    out = _call(client, "mempalace_add_drawer",
                {"wing": "Web", "room": "t", "content": "spoof test", "bogus_internal": "x"})
    assert out["success"] is True


def test_mcp_status_counts_articles_not_passages(client):
    _call(client, "mempalace_add_drawer", {"wing": "Web", "room": "t", "content": _LONG})
    status = _call(client, "mempalace_status", {})
    assert status["total_drawers"] == 1
    assert status["wings"] == {"Web": 1}
