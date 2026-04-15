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


def test_get_drawer_by_id(client):
    ids = _seed_drawers(client)
    resp = client.get(f"/api/drawers/{ids[0]}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["drawer_id"] == ids[0]
    assert "Python Tutorial" in data["content"]
    assert data["wing"] == "Web"
    assert data["room"] == "Captures"


def test_get_drawer_not_found(client):
    resp = client.get("/api/drawers/nonexistent-id")
    assert resp.status_code == 404
