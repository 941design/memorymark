# S3: Read endpoint tests (search, status, taxonomy, wings, rooms, by-url)


def _seed_drawers(client):
    """Create a set of drawers for read endpoint tests. Returns list of drawer IDs."""
    ids = []
    r1 = client.post("/api/drawers", json={
        "wing": "Web",
        "room": "Captures",
        "content": "# Python Tutorial\nLearn Python programming basics and advanced topics.",
        "metadata": {
            "source_url": "https://example.com/python",
            "title": "Python Tutorial",

            "timestamp": "2026-04-13T01:00:00Z",
        },
    })
    ids.append(r1.json()["drawer_id"])

    r2 = client.post("/api/drawers", json={
        "wing": "Web",
        "room": "Articles",
        "content": "# Rust Guide\nGetting started with Rust programming language.",
        "metadata": {
            "source_url": "https://example.com/rust",
            "title": "Rust Guide",

            "timestamp": "2026-04-13T02:00:00Z",
        },
    })
    ids.append(r2.json()["drawer_id"])

    r3 = client.post("/api/drawers", json={
        "wing": "Research",
        "room": "Papers",
        "content": "# Machine Learning Paper\nDeep learning approaches for natural language processing.",
        "metadata": {
            "source_url": "https://example.com/ml-paper",
            "title": "ML Paper",

            "timestamp": "2026-04-13T03:00:00Z",
        },
    })
    ids.append(r3.json()["drawer_id"])
    return ids


def test_search_returns_results(client):
    _seed_drawers(client)
    resp = client.get("/api/search", params={"query": "Python programming"})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data


def test_search_with_limit(client):
    _seed_drawers(client)
    resp = client.get("/api/search", params={"query": "programming", "limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data.get("results", [])) <= 1


def test_search_with_wing_filter(client):
    _seed_drawers(client)
    resp = client.get("/api/search", params={"query": "programming", "wing": "Web"})
    assert resp.status_code == 200
    data = resp.json()
    for r in data.get("results", []):
        assert r.get("wing") == "Web"


def test_search_requires_query(client):
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_status_returns_palace_info(client):
    _seed_drawers(client)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_drawers"] == 3
    assert "wings" in data
    assert "rooms" in data


def test_status_empty_palace(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_drawers"] == 0


def test_taxonomy_returns_hierarchy(client):
    _seed_drawers(client)
    resp = client.get("/api/taxonomy")
    assert resp.status_code == 200
    data = resp.json()
    taxonomy = data["taxonomy"]
    assert "Web" in taxonomy
    assert "Captures" in taxonomy["Web"]
    assert "Articles" in taxonomy["Web"]
    assert "Research" in taxonomy
    assert "Papers" in taxonomy["Research"]


def test_wings_returns_wing_counts(client):
    _seed_drawers(client)
    resp = client.get("/api/wings")
    assert resp.status_code == 200
    data = resp.json()
    assert "Web" in data["wings"]
    assert data["wings"]["Web"] == 2
    assert "Research" in data["wings"]
    assert data["wings"]["Research"] == 1


def test_rooms_returns_all_rooms(client):
    _seed_drawers(client)
    resp = client.get("/api/rooms")
    assert resp.status_code == 200
    data = resp.json()
    assert "Captures" in data["rooms"]
    assert "Articles" in data["rooms"]
    assert "Papers" in data["rooms"]


def test_rooms_filters_by_wing(client):
    _seed_drawers(client)
    resp = client.get("/api/rooms", params={"wing": "Web"})
    assert resp.status_code == 200
    data = resp.json()
    assert "Captures" in data["rooms"]
    assert "Articles" in data["rooms"]
    assert "Papers" not in data["rooms"]


def test_by_url_found(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers/by-url", params={"url": "https://example.com/python"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert "drawer_id" in data
    assert data["title"] == "Python Tutorial"


def test_by_url_not_found(client):
    resp = client.get("/api/drawers/by-url", params={"url": "https://example.com/nonexistent"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False


def test_by_url_requires_url_param(client):
    resp = client.get("/api/drawers/by-url")
    assert resp.status_code == 422


def test_by_url_returns_content(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers/by-url", params={"url": "https://example.com/python", "include_content": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert "content" in data
    assert "Python Tutorial" in data["content"]


def test_by_url_omits_content_by_default(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers/by-url", params={"url": "https://example.com/python"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert "content" not in data


def test_by_url_finds_mcp_created_drawer(client):
    """Regression: /api/drawers/by-url must find drawers added through the MCP
    add_drawer tool, which only persists provenance in source_file (not
    source_url). Without an OR query, MCP-created drawers stay invisible to
    duplicate detection, /summarize, and get_page_content.
    """
    from mempalace.mcp_server import tool_add_drawer

    url = "https://example.com/mcp-created"
    result = tool_add_drawer(
        wing="Web",
        room="Captures",
        content="# MCP\n\nBody",
        source_file=url,
        added_by="mcp",
    )
    assert result["success"] is True

    resp = client.get("/api/drawers/by-url", params={"url": url})
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is True
    assert data["drawer_id"] == result["drawer_id"]


def test_get_drawer_by_id(client):
    ids = _seed_drawers(client)
    resp = client.get(f"/api/drawers/{ids[0]}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["drawer_id"] == ids[0]
    assert "Python Tutorial" in data["content"]
    assert data["wing"] == "Web"
    assert data["room"] == "Captures"


def test_get_drawer_returns_full_contract(client):
    """GET /api/drawers/{id} must include drawer_id, content, wing, room,
    added_by, content_hash, and normalized metadata with source_url/title/timestamp.
    """
    import hashlib
    ids = _seed_drawers(client)
    resp = client.get(f"/api/drawers/{ids[0]}")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("drawer_id", "content", "wing", "room", "added_by", "content_hash", "metadata"):
        assert key in data, f"missing top-level key: {key}"
    assert data["content_hash"] == hashlib.sha256(data["content"].encode()).hexdigest()
    meta = data["metadata"]
    for key in ("source_url", "title", "timestamp"):
        assert key in meta, f"missing metadata key: {key}"
    assert meta["source_url"] == "https://example.com/python"
    assert meta["title"] == "Python Tutorial"
    assert meta["timestamp"] == "2026-04-13T01:00:00Z"


def test_get_drawer_not_found(client):
    resp = client.get("/api/drawers/nonexistent-id")
    assert resp.status_code == 404


def test_list_drawers_returns_all(client):
    ids = _seed_drawers(client)
    resp = client.get("/api/drawers")
    assert resp.status_code == 200
    data = resp.json()
    assert "drawers" in data
    assert len(data["drawers"]) == 3
    assert data["count"] == 3


def test_list_drawers_filters_by_wing(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers", params={"wing": "Web"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["drawers"]) == 2
    for d in data["drawers"]:
        assert d["wing"] == "Web"


def test_list_drawers_filters_by_room(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers", params={"room": "Captures"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["drawers"]) == 1
    assert data["drawers"][0]["room"] == "Captures"


def test_list_drawers_filters_by_wing_and_room(client):
    _seed_drawers(client)
    resp = client.get("/api/drawers", params={"wing": "Web", "room": "Captures"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["drawers"]) == 1
    assert data["drawers"][0]["wing"] == "Web"
    assert data["drawers"][0]["room"] == "Captures"


def test_list_drawers_empty_palace(client):
    resp = client.get("/api/drawers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["drawers"] == []
    assert data["count"] == 0


def test_list_drawers_contract(client):
    """GET /api/drawers must return per-drawer drawer_id, wing, room, content_preview."""
    ids = _seed_drawers(client)
    resp = client.get("/api/drawers")
    assert resp.status_code == 200
    data = resp.json()
    drawers = data["drawers"]
    assert len(drawers) >= 1
    first = drawers[0]
    for key in ("drawer_id", "wing", "room", "content_preview"):
        assert key in first, f"missing key: {key}"
    assert first["drawer_id"] == ids[0]
    assert first["wing"] == "Web"
    assert first["room"] == "Captures"
    assert "Python Tutorial" in first["content_preview"]


def test_search_result_contract(client):
    """GET /api/search must return per-result drawer_id, content, metadata,
    distance, wing, room, added_by."""
    _seed_drawers(client)
    resp = client.get("/api/search", params={"query": "Python programming"})
    assert resp.status_code == 200
    results = resp.json().get("results", [])
    assert len(results) >= 1
    first = results[0]
    for key in ("drawer_id", "content", "metadata", "distance", "wing", "room", "added_by"):
        assert key in first, f"missing per-result key: {key}"
    assert isinstance(first["metadata"], dict)
    assert "source_url" in first["metadata"]
    assert "title" in first["metadata"]
    assert "timestamp" in first["metadata"]
    assert isinstance(first["distance"], (int, float))
    assert first["added_by"] == "memorymark"
