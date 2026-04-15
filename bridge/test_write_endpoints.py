# S2: Write endpoint tests (POST, PUT, DELETE /api/drawers)


def _create_test_drawer(client, **overrides):
    """Helper to create a drawer and return the response."""
    payload = {
        "wing": "Web",
        "room": "Captures",
        "content": "# Test Page\nSome content here.",
        "metadata": {
            "source_url": "https://example.com/test",
            "title": "Test Page",
            "timestamp": "2026-04-13T00:00:00Z",
        },
    }
    payload.update(overrides)
    return client.post("/api/drawers", json=payload)


def test_create_drawer_success(client):
    resp = _create_test_drawer(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert "drawer_id" in data


def test_create_drawer_stores_custom_metadata(client):
    resp = _create_test_drawer(client)
    assert resp.status_code == 201
    drawer_id = resp.json()["drawer_id"]

    # Verify custom metadata via by-url lookup
    resp2 = client.get("/api/drawers/by-url", params={"url": "https://example.com/test"})
    data = resp2.json()
    assert data["exists"] is True
    assert data["drawer_id"] == drawer_id
    assert data["title"] == "Test Page"


def test_create_drawer_increments_health_count(client):
    assert client.get("/api/health").json()["drawers"] == 0
    _create_test_drawer(client)
    assert client.get("/api/health").json()["drawers"] == 1


def test_create_drawer_without_metadata(client):
    resp = client.post("/api/drawers", json={
        "wing": "Web",
        "room": "Captures",
        "content": "# Bare content",
    })
    assert resp.status_code == 201
    assert resp.json()["success"] is True


def test_create_drawer_idempotent(client):
    """Creating the same drawer twice returns success (MemPalace dedup by content hash)."""
    resp1 = _create_test_drawer(client)
    resp2 = _create_test_drawer(client)
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["drawer_id"] == resp2.json()["drawer_id"]


def test_create_drawer_missing_required_fields(client):
    resp = client.post("/api/drawers", json={"wing": "Web"})
    assert resp.status_code == 422  # Pydantic validation error


def test_update_drawer_content(client):
    create_resp = _create_test_drawer(client)
    drawer_id = create_resp.json()["drawer_id"]

    resp = client.put(f"/api/drawers/{drawer_id}", json={"content": "# Updated\nNew content."})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["drawer_id"] == drawer_id


def test_update_drawer_metadata(client):
    create_resp = _create_test_drawer(client)
    drawer_id = create_resp.json()["drawer_id"]

    resp = client.put(f"/api/drawers/{drawer_id}", json={
        "metadata": {"title": "Updated Title"},
    })
    assert resp.status_code == 200

    # Verify the title was updated via by-url
    by_url = client.get("/api/drawers/by-url", params={"url": "https://example.com/test"})
    assert by_url.json()["title"] == "Updated Title"


def test_update_drawer_not_found(client):
    resp = client.put("/api/drawers/nonexistent_drawer_id", json={"content": "test"})
    assert resp.status_code == 404


def test_delete_drawer_success(client):
    create_resp = _create_test_drawer(client)
    drawer_id = create_resp.json()["drawer_id"]

    resp = client.delete(f"/api/drawers/{drawer_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Verify drawer count went back to 0
    assert client.get("/api/health").json()["drawers"] == 0


def test_delete_drawer_not_found(client):
    resp = client.delete("/api/drawers/nonexistent_drawer_id")
    assert resp.status_code == 404


def test_create_drawer_writes_all_metadata_atomically(client):
    """A single drawer create must persist core + custom metadata in one shot.

    Regression: previously the bridge did tool_add_drawer() then a second
    col.update() for custom fields. If the second step failed the drawer was
    stored with missing source_url/title/timestamp.
    """
    resp = _create_test_drawer(client)
    assert resp.status_code == 201
    drawer_id = resp.json()["drawer_id"]

    full = client.get(f"/api/drawers/{drawer_id}").json()
    assert full["wing"] == "Web"
    assert full["room"] == "Captures"
    assert full["title"] == "Test Page"
    assert full["source_url"] == "https://example.com/test"


def test_update_drawer_preserves_custom_metadata_when_only_content_changes(client):
    """Updating only content must not wipe source_url/title."""
    create_resp = _create_test_drawer(client)
    drawer_id = create_resp.json()["drawer_id"]

    resp = client.put(f"/api/drawers/{drawer_id}", json={"content": "# Changed"})
    assert resp.status_code == 200

    full = client.get(f"/api/drawers/{drawer_id}").json()
    assert full["title"] == "Test Page"
    assert full["source_url"] == "https://example.com/test"


def test_update_drawer_source_url_keeps_source_file_in_sync(client):
    """source_file (MemPalace core) and source_url (bridge custom) must match."""
    create_resp = _create_test_drawer(client)
    drawer_id = create_resp.json()["drawer_id"]

    resp = client.put(
        f"/api/drawers/{drawer_id}",
        json={"metadata": {"source_url": "https://example.com/renamed"}},
    )
    assert resp.status_code == 200

    # by-url lookup uses source_url — it must resolve to this drawer.
    by_url = client.get(
        "/api/drawers/by-url", params={"url": "https://example.com/renamed"}
    ).json()
    assert by_url["exists"] is True
    assert by_url["drawer_id"] == drawer_id
