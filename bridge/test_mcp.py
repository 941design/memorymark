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
