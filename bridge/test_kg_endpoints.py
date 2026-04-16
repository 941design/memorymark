# S5: Knowledge Graph endpoint tests


def test_kg_add_fact(client):
    resp = client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert "triple_id" in data
    assert data["fact"] == "Alice → works_at → Acme"


def test_kg_add_fact_with_optional_fields(client):
    resp = client.post("/api/kg/facts", json={
        "subject": "Bob",
        "predicate": "started_school",
        "object": "Year 7",
        "valid_from": "2026-09-01",
        "source_closet": "drawer_web_captures_abc123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True


def test_kg_query_entity(client):
    client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
    })
    resp = client.get("/api/kg/query", params={"entity": "Alice"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity"] == "Alice"
    assert data["count"] >= 1
    facts = data["facts"]
    assert any(f["predicate"] == "works_at" and f["object"] == "Acme" for f in facts)


def test_kg_query_with_direction(client):
    client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
    })
    resp = client.get("/api/kg/query", params={"entity": "Alice", "direction": "outgoing"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(f["direction"] == "outgoing" for f in data["facts"])


def test_kg_query_unknown_entity(client):
    resp = client.get("/api/kg/query", params={"entity": "NonExistent"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["facts"] == []


def test_kg_query_requires_entity(client):
    resp = client.get("/api/kg/query")
    assert resp.status_code == 422


def test_kg_invalidate_fact(client):
    client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
    })
    resp = client.post("/api/kg/invalidate", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    # Verify the fact is now marked as not current (use outgoing to avoid duplicates)
    query = client.get("/api/kg/query", params={"entity": "Alice", "direction": "outgoing"})
    facts = query.json()["facts"]
    match = [f for f in facts if f["predicate"] == "works_at" and f["object"] == "Acme"]
    assert len(match) == 1
    assert match[0]["current"] is False
    assert match[0]["valid_to"] is not None


def test_kg_invalidate_with_ended_date(client):
    client.post("/api/kg/facts", json={
        "subject": "Bob",
        "predicate": "lives_in",
        "object": "Berlin",
    })
    resp = client.post("/api/kg/invalidate", json={
        "subject": "Bob",
        "predicate": "lives_in",
        "object": "Berlin",
        "ended": "2026-03-01",
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_kg_add_fact_missing_required_field(client):
    resp = client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
    })
    assert resp.status_code == 422


def test_kg_invalidate_missing_required_field(client):
    resp = client.post("/api/kg/invalidate", json={
        "subject": "Alice",
        "predicate": "works_at",
    })
    assert resp.status_code == 422


def test_kg_query_with_as_of(client):
    client.post("/api/kg/facts", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
        "valid_from": "2025-01-01",
    })
    client.post("/api/kg/invalidate", json={
        "subject": "Alice",
        "predicate": "works_at",
        "object": "Acme",
        "ended": "2025-06-01",
    })
    # Query as of a date when the fact was still valid
    resp = client.get("/api/kg/query", params={"entity": "Alice", "as_of": "2025-03-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["as_of"] == "2025-03-01"
