# S6: Diary endpoint tests (POST /api/diary/write)


def test_diary_write_success(client):
    resp = client.post("/api/diary/write", json={
        "agent_name": "daydreamer",
        "entry": "SESSION:2026-04-16|observed.drawer.drift|★★",
        "topic": "synthesis",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert data["agent"] == "daydreamer"
    assert data["topic"] == "synthesis"
    assert "entry_id" in data
    assert "timestamp" in data


def test_diary_write_defaults_topic_to_general(client):
    resp = client.post("/api/diary/write", json={
        "agent_name": "daydreamer",
        "entry": "just checking in",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["topic"] == "general"


def test_diary_write_requires_agent_name(client):
    resp = client.post("/api/diary/write", json={"entry": "oops"})
    assert resp.status_code == 422


def test_diary_write_requires_entry(client):
    resp = client.post("/api/diary/write", json={"agent_name": "daydreamer"})
    assert resp.status_code == 422


def test_diary_write_emits_wal_entry(client):
    """POST /api/diary/write must produce a diary_write WAL entry (no MCP fallback)."""
    import json
    import mempalace.mcp_server as _mp

    resp = client.post("/api/diary/write", json={
        "agent_name": "daydreamer",
        "entry": "first diary entry",
        "topic": "synthesis",
    })
    assert resp.status_code == 201

    with open(str(_mp._WAL_FILE)) as fh:
        lines = fh.readlines()
    entry = json.loads(lines[-1])
    assert entry["operation"] == "diary_write"
    params = entry["params"]
    assert params["agent_name"] == "daydreamer"
    assert params["topic"] == "synthesis"
    assert "entry_id" in params
