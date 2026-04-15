# Acceptance Criteria — Docker Bridge

## AC-1: Project scaffold exists
- `bridge/server.py` contains a FastAPI application instance
- `bridge/requirements.txt` lists mempalace, fastapi, uvicorn, and test dependencies
- `bridge/Dockerfile` builds from python:3.11-slim, installs requirements, runs uvicorn on port 21517
- `docker-compose.yml` defines `memorymark-bridge` service with port 127.0.0.1:21517, mempalace-data volume, MEMORYMARK_PORT env var

## AC-2: Health endpoint returns status
- `GET /api/health` returns `{"status": "ok", "drawers": <int>}` with HTTP 200

## AC-3: CORS allows extension origins
- Responses include CORS headers allowing `chrome-extension://*` and `moz-extension://*` origins

## AC-4: POST /api/drawers creates a drawer
- Accepts JSON body: `{wing, room, content, metadata: {source_url, title, hall, timestamp}}`
- Stores drawer in MemPalace with custom metadata fields (source_url, title, hall, timestamp) persisted in ChromaDB
- Returns `{success: true, drawer_id: "..."}` with HTTP 201

## AC-5: PUT /api/drawers/{id} updates a drawer
- Accepts optional `content` and `metadata` fields
- Returns `{success: true, drawer_id: "..."}` with HTTP 200
- Returns HTTP 404 if drawer not found

## AC-6: DELETE /api/drawers/{id} removes a drawer
- Returns `{success: true}` with HTTP 200
- Returns HTTP 404 if drawer not found

## AC-7: GET /api/search returns matching drawers
- Accepts query params: `query` (required), `wing`, `room`, `max_distance`, `limit`
- Returns search results with text, wing, room, similarity, distance

## AC-8: GET /api/status returns palace statistics
- Returns total_drawers count, wings, rooms

## AC-9: GET /api/taxonomy returns wing/room hierarchy
- Returns nested structure: `{taxonomy: {wing: {room: count}}}`

## AC-10: GET /api/wings lists all wings
- Returns `{wings: {name: count}}`

## AC-11: GET /api/rooms lists rooms
- Accepts optional `wing` query param
- Returns `{rooms: {name: count}}`

## AC-12: GET /api/drawers/by-url detects duplicates
- Accepts `url` query param
- Returns `{exists: true, drawer_id: "...", title: "..."}` when found
- Returns `{exists: false}` when not found
