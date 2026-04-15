# Feature 01: Docker Bridge — MemPalace HTTP API

## Context

From the main spec (sorted-imagining-journal.md), Section 3.1 and Section 6.

MemoryMark needs a local HTTP bridge that wraps the MemPalace Python library and exposes it via REST endpoints. This runs in Docker and is the foundation all other features depend on.

## Requirements

### Docker Setup

- `docker-compose.yml` at project root with service `memorymark-bridge`
- `bridge/Dockerfile` — Python 3.11-slim, installs mempalace + fastapi + uvicorn
- `bridge/requirements.txt` — mempalace, fastapi, uvicorn
- `bridge/server.py` — FastAPI application
- Port: `21517` on localhost (configurable via env var `MEMORYMARK_PORT`)
- Volume: `mempalace-data` mounted at `/root/.mempalace/palace`
- Restart policy: `unless-stopped`

### docker-compose.yml

```yaml
services:
  memorymark-bridge:
    build: ./bridge
    ports:
      - "127.0.0.1:21517:21517"
    volumes:
      - mempalace-data:/root/.mempalace/palace
    environment:
      - MEMORYMARK_PORT=21517
    restart: unless-stopped

volumes:
  mempalace-data:
```

### bridge/Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install mempalace fastapi uvicorn
COPY server.py .
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "21517"]
```

### FastAPI Bridge Endpoints (bridge/server.py)

The bridge wraps MemPalace's Python API directly (imports `mempalace` as a library, does NOT shell out to the MCP server).

**Write endpoints:**

| Method | Path | Maps to | Parameters |
|--------|------|---------|------------|
| `POST` | `/api/drawers` | `mempalace_add_drawer` | `wing`, `room`, `content`, `metadata` (incl. `source_url`, `title`, `hall`, `timestamp`) |
| `PUT` | `/api/drawers/{id}` | `mempalace_update_drawer` | `content?`, `metadata?` |
| `DELETE` | `/api/drawers/{id}` | `mempalace_delete_drawer` | — |

**Read endpoints:**

| Method | Path | Maps to | Parameters |
|--------|------|---------|------------|
| `GET` | `/api/search` | `mempalace_search` | `query`, `wing?`, `room?`, `max_distance?`, `limit?` |
| `GET` | `/api/status` | `mempalace_status` | — |
| `GET` | `/api/taxonomy` | `mempalace_get_taxonomy` | — |
| `GET` | `/api/wings` | `mempalace_list_wings` | — |
| `GET` | `/api/rooms` | `mempalace_list_rooms` | `wing?` |
| `GET` | `/api/drawers/by-url` | custom (search metadata) | `url` |

**Duplicate detection endpoint:**

| Method | Path | Purpose | Parameters |
|--------|------|---------|------------|
| `GET` | `/api/drawers/by-url` | Check if URL already captured | `url` (exact match on `source_url` in metadata) |

Returns `{ "exists": true, "drawer_id": "...", "title": "..." }` or `{ "exists": false }`.

**CORS**: Allow `chrome-extension://*` and `moz-extension://*` origins.

**Health check**: `GET /api/health` → `{ "status": "ok", "drawers": 1234 }`.

## Verification

```bash
docker compose up -d
curl http://localhost:21517/api/health
curl -X POST http://localhost:21517/api/drawers \
  -H 'Content-Type: application/json' \
  -d '{"wing":"Web","room":"Captures","content":"# Test\nHello","metadata":{"source_url":"https://example.com","title":"Test"}}'
curl 'http://localhost:21517/api/drawers/by-url?url=https://example.com'
curl 'http://localhost:21517/api/search?query=test&limit=5'
```
