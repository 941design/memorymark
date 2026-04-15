# S1: Scaffold, health, and CORS tests


def test_health_returns_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["drawers"], int)
    assert data["drawers"] >= 0


def test_health_drawers_count_starts_at_zero(client):
    resp = client.get("/api/health")
    assert resp.json()["drawers"] == 0


def test_cors_allows_chrome_extension(client):
    resp = client.options(
        "/api/health",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abcdefghijklmnop"


def test_cors_allows_moz_extension(client):
    resp = client.options(
        "/api/health",
        headers={
            "Origin": "moz-extension://abcdefghijklmnop",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "moz-extension://abcdefghijklmnop"


def test_cors_rejects_random_origin(client):
    resp = client.options(
        "/api/health",
        headers={
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_cors_rejects_localhost_browser_page(client):
    # Localhost browser pages must not be granted CORS access — any local dev
    # app could otherwise read/write the user's palace from JS.
    resp = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_cors_rejects_127_0_0_1_browser_page(client):
    resp = client.options(
        "/api/drawers",
        headers={
            "Origin": "http://127.0.0.1:8080",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers
