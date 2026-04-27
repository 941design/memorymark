# Feature Spec: Document Chunking for Long Captures

## Problem Statement

The bridge's `sanitize_content` caps drawer content at 100 000 characters. Long web pages — notably arxiv HTML papers, long-form essays, technical documentation, and conference transcripts — routinely exceed this cap and fail capture with `400: content exceeds maximum length of 100000 characters`. The extension's current truncation strategy in `content.js` silently drops the tail of the document (and even then, because the header is prepended *after* truncation, composed `content` still overflows and the capture hard-fails).

Beyond the size cap, storing a 100 000-character document as a single ChromaDB drawer is also bad for retrieval quality: the default embedding model has an input window of ~8 000 tokens, so long documents are silently truncated by the embedder and only the first portion is reflected in the stored vector. Search results for passages past that boundary are effectively unreachable.

Chunking solves both problems: it removes the hard failure on long pages *and* improves semantic retrieval by letting each passage contribute its own embedding.

## Goals

1. Capture any web page regardless of length (up to a very large ceiling, e.g. 5 MB of Markdown).
2. Preserve full content verbatim — no lossy summarization or truncation.
3. Improve retrieval quality on long documents by embedding passage-sized chunks.
4. Keep the user-facing "one page = one capture" mental model intact.
5. Ship with no breaking change to existing REST clients; older single-drawer captures keep working.

## Non-Goals

- Manual chunk editing or reordering in the UI.
- Cross-document chunk fusion (RAG-style long-context reranking — future work).
- Reprocessing old single-drawer captures into chunks (migration covered separately).
- Streaming partial chunks to Gemma during capture.

## Core Functionality

When the extension captures a page, it sends the full Markdown document to a new bridge endpoint `POST /api/documents`. The bridge splits the document into chunks at paragraph/heading boundaries, targeting ~4 000 characters per chunk with ~500 character overlap, and writes each chunk as a drawer that shares a stable `document_id`. Lookup, search, deletion, and the `get_page_content` tool treat the chunks as one document.

## Architecture & Data Model

### Chunk storage

Each chunk is a ChromaDB drawer with this extended metadata:

| Field              | Type   | Notes                                                                 |
|--------------------|--------|-----------------------------------------------------------------------|
| `wing`             | str    | As today (`Web`).                                                     |
| `room`             | str    | As today (`Captures`).                                                |
| `source_url`       | str    | Identical on every chunk of the same document.                        |
| `source_file`      | str    | Mirrors `source_url` for mempalace compatibility.                     |
| `title`            | str    | Identical on every chunk.                                             |
| `timestamp`        | str    | ISO-8601 capture time, identical on every chunk.                      |
| `document_id`      | str    | Stable ID shared by all chunks of a document. See "Document ID".      |
| `chunk_index`      | int    | 0-based position of this chunk within the document.                   |
| `chunk_count`      | int    | Total number of chunks in the document (for sanity checks in search). |
| `chunk_start`      | int    | Character offset of the chunk's first non-overlap char in the source. |
| `chunk_end`        | int    | Character offset of the chunk's last non-overlap char in the source.  |
| `document_hash`    | str    | SHA-256 of full document content (provenance + idempotency).          |
| `added_by`         | str    | As today.                                                             |
| `filed_at`         | str    | Per-chunk write time (may vary by milliseconds across chunks).        |

### Drawer ID

```
drawer_{wing}_{room}_{document_hash[:16]}_{chunk_index:04d}
```

The 16-char document hash prefix groups chunks; the zero-padded chunk index keeps drawer IDs sortable. Re-capturing the same URL with identical content produces identical drawer IDs across all chunks → upsert is a no-op (idempotent).

### Document ID

```
document_id = "doc_" + sha256(source_url + document_hash)[:24]
```

Encodes both URL and content so a re-capture of the same URL with new content yields a new `document_id` (old chunks become orphaned and can be garbage-collected — see "Deletion").

## Chunking Algorithm

Input: full Markdown document (string).
Output: ordered list of `{content, chunk_start, chunk_end}`.

1. **Target size**: `CHUNK_TARGET = 4000` chars. **Max size**: `CHUNK_MAX = 8000`. **Overlap**: `CHUNK_OVERLAP = 500`.
2. If `len(doc) <= CHUNK_TARGET`, emit a single chunk (chunk_index=0, chunk_count=1) — degenerate case, stays cheap for short pages.
3. Otherwise, walk the document and emit chunks preferring splits at these boundaries, in order of preference:
   a. Blank line preceding a Markdown heading (`^#{1,6} `).
   b. Blank line (paragraph break).
   c. Single newline.
   d. Sentence boundary (`. ` / `! ` / `? ` followed by uppercase).
   e. Word boundary.
   f. Hard cut at `CHUNK_MAX`.
4. Each chunk is emitted with `CHUNK_OVERLAP` chars from the end of the previous chunk prepended (except the first). The overlap is stored as part of chunk content for retrieval but does **not** count toward `chunk_start` — the offset reflects the non-overlap region.
5. Code fences (``` ``` ```) must not be split mid-block; if a split boundary falls inside a fence, extend the chunk to the closing fence even if it exceeds `CHUNK_TARGET` (still bounded by `CHUNK_MAX`).
6. Pre-append the standard header (`# {title}\n\n> Source: {url}\n\n`) to chunk 0 only — subsequent chunks start at the boundary following the header region.

## API Changes

### New: `POST /api/documents`

Replaces the capture-time use of `POST /api/drawers` for extension clients. Accepts the full document; the bridge handles chunking.

Request:
```json
{
  "wing": "Web",
  "room": "Captures",
  "content": "…full markdown…",
  "added_by": "memorymark",
  "metadata": {
    "source_url": "https://arxiv.org/html/2604.14228v1",
    "title": "Some Paper",
    "timestamp": "2026-04-21T10:00:00Z"
  }
}
```

Response (201):
```json
{
  "success": true,
  "document_id": "doc_…",
  "chunk_count": 14,
  "drawer_ids": ["drawer_Web_Captures_abc…_0000", "…_0001", …]
}
```

Errors:
- `400` if wing/room sanitation fails or content is empty.
- `413` if `len(content) > DOCUMENT_MAX_BYTES` (default 5 MB).
- `500` partial-write recovery: if some chunks upsert and then one fails, the bridge must delete already-written chunks before returning `500`. Response body includes `partial_drawer_ids` that were rolled back for observability.

### Modified: `POST /api/drawers`

Unchanged shape and semantics. Remains the canonical endpoint for agent/MCP callers who want to write a single drawer explicitly (notes, diary synthesis, etc.). Does **not** chunk. Documents this in its OpenAPI description.

### Modified: `GET /api/drawers/by-url`

Returns aggregate document info when any chunk matches the URL.

Request: `?url=…&include_content=false` (unchanged).
Response:
```json
{
  "exists": true,
  "document_id": "doc_…",
  "drawer_id": "drawer_…_0000",  // chunk 0 for backward compat
  "chunk_count": 14,
  "title": "Some Paper"
}
```

With `include_content=true`, `content` is the **assembled** document (overlap regions deduplicated). Older callers that only read `exists`/`drawer_id`/`title` keep working.

### New: `GET /api/documents/{document_id}`

Returns assembled document metadata and full content (chunks joined by `chunk_index`, overlap stripped). Used by Gemma's `get_page_content` tool and any future "view saved page" UI.

### Modified: `GET /api/search`

Results are deduplicated by `document_id` — the bridge keeps the single best-scoring chunk per document. Each result gains:
```json
{
  "document_id": "doc_…",
  "chunk_index": 7,
  "chunk_count": 14,
  "matched_excerpt": "…"
}
```
A new optional query param `group=chunk` disables deduplication for advanced callers (e.g. RAG agents that want all top-N passages).

### New: `DELETE /api/documents/{document_id}`

Deletes every chunk of a document in one call. Cascade deletes associated KG triples where `source_drawer_id` is any chunk of the document (future-proofing for the KG integration spec).

### Modified: `DELETE /api/drawers/{drawer_id}`

If the drawer is part of a multi-chunk document (`chunk_count > 1`), the bridge returns `409 Conflict` with a body pointing at the document-level endpoint:
```json
{ "detail": "Drawer is part of a document; delete via /api/documents/{id}", "document_id": "doc_…" }
```
Single-chunk drawers (legacy or agent-written) delete as before.

## Capture Flow (Extension)

1. `content.js` extracts the page (as today) but **stops truncating** — produces full Markdown regardless of length.
2. If `len(content) > DOCUMENT_MAX_BYTES` (5 MB), extension truncates to that ceiling with a user-visible warning in the overlay ("Page truncated to 5 MB — tail content not captured").
3. `background.js:handleExtractResult` calls `POST /api/documents` (not `POST /api/drawers`).
4. Overlay states extended:
   - `saving` — unchanged.
   - `saved` — shows title plus `chunk_count` when > 1 (e.g. "Saved in 14 chunks").
   - `already-exists` — unchanged.
   - `error` — shows `detail` from bridge (already implemented).
   - `too-large` — new: shown only when the 5 MB ceiling is hit.
5. The "already saved" badge logic (`updateBadgeForTab`) is unchanged — `by-url` still reports `exists: true` for any chunk match.

## Search Behavior

- Default: one result per document, showing the best-scoring chunk with a short excerpt around the match.
- With `group=chunk`: all matching chunks returned, capped by `limit`.
- `wing`/`room` filters continue to work against each chunk's metadata.
- A new `document_hits` field in the response summarizes how many underlying chunks matched per returned document (useful for ranking UIs).

## Gemma Tool Updates

- `get_page_content` (in `offscreen.js`) calls `GET /api/documents/{id}` instead of `GET /api/drawers/{id}` when `chunk_count > 1`. For single-chunk documents it stays on the drawer endpoint for backward compat.
- `search_memory` continues to use `/api/search` (default grouped mode). Tool output includes `chunk_count` so Gemma can explain "this is a long doc with N chunks" if asked.

## Backward Compatibility

- Existing single-drawer captures (no `document_id`, `chunk_index=0`, no `chunk_count`) are treated as `chunk_count=1` documents. The bridge synthesizes a `document_id` from `source_url` on read when absent, so `GET /api/documents/{id}` and `/api/drawers/by-url` work uniformly across old and new data.
- `POST /api/drawers` keeps identical behavior for agent callers.
- No schema migration required on the ChromaDB collection — all new fields are additive and optional.

## Critical Constraints

- Total capture time for a 100 000-char document must not exceed 2× the current baseline (measured on a representative arxiv paper). Chunking is done in Python on the bridge, not in the embedder loop; embeddings are computed in batch.
- Chunk writes must be atomic per document: all chunks committed, or none. If any `col.upsert` fails, the bridge rolls back previously written chunks in the same request.
- SSE `drawer-added` events fire **once per document**, not once per chunk — listeners (future capture-history popup) receive one logical event with `chunk_count` attached. (The per-chunk writes still happen but are coalesced at the event layer.)
- `sanitize_content` at the drawer layer is unchanged — per-chunk length stays well below 100 000 by construction (`CHUNK_MAX = 8000`), so the cap is never hit.
- Overlap regions must not be double-counted when assembling full-document content for `GET /api/documents/{id}` or `include_content=true` on by-url.
- Rechunking is deterministic: identical input document produces identical chunks (and therefore identical drawer IDs), so recaptures are no-ops.

## Integration Points

- `bridge/server.py` — new `/api/documents` endpoints, modified `/api/search`, `/api/drawers/by-url`, `/api/drawers/{id}` deletion guard.
- `bridge/chunker.py` (new) — pure function `chunk_document(markdown, target, max, overlap) -> list[Chunk]`, unit-testable without the full FastAPI stack.
- `bridge/events.py` — coalesce per-chunk add events into one `document-added` SSE event.
- `extension/content.js` — remove the 100 000-char truncation; add the `too-large` overlay state for the 5 MB ceiling.
- `extension/background.js` — switch capture path from `POST /api/drawers` to `POST /api/documents`; surface `chunk_count` in the overlay.
- `extension/offscreen.js` — `get_page_content` tool uses `/api/documents/{id}` when multi-chunk.
- `docs/rest-api.md` — document the new endpoints, updated search behavior, and the single-drawer-vs-document contract for agent writers.

## Testing Strategy

### Unit (bridge, `pytest`)

1. `test_chunker.py`:
   - Short doc (< target) → single chunk, no overlap.
   - Doc with heading boundaries → splits at headings when possible.
   - Doc with no boundaries (one long paragraph) → falls through to sentence/word cuts.
   - Code fences never split mid-block.
   - Identical input → identical chunk list (determinism).
   - Chunk overlap assembles back to original when overlap regions are stripped.
2. `test_write_endpoints.py`:
   - `POST /api/documents` on an arxiv-sized input (200 000 chars) returns 201 with `chunk_count > 1`.
   - Idempotency: posting the same document twice returns the same `document_id` and `drawer_ids`.
   - `DELETE /api/drawers/{id}` on a chunk → 409 with pointer to document endpoint.
   - `DELETE /api/documents/{id}` removes every chunk; `by-url` then reports `exists: false`.
   - Partial-write rollback: force a `col.upsert` failure on chunk N; assert chunks 0..N-1 are deleted and endpoint returns 500 with `partial_drawer_ids`.
3. `test_read_endpoints.py`:
   - `GET /api/drawers/by-url?include_content=true` on a chunked document returns assembled content matching the original input byte-for-byte.
   - `GET /api/search` returns one hit per document by default; `group=chunk` returns all matching chunks.
4. `test_write_endpoints.py::test_create_drawer_rejects_oversized_content` (existing) — still passes, proving `POST /api/drawers` keeps the single-drawer cap for agent callers.

### Integration

- End-to-end test: capture `https://arxiv.org/html/2604.14228v1` via a fixture HTML file; assert `chunk_count >= 5`, `by-url` returns the assembled content, `/search` with a term from late in the paper returns a hit.
- Regression: capture a small page (< 4 KB); assert `chunk_count == 1` and drawer shape matches today's output.

### Manual

- Run the Firefox build against a live arxiv page; confirm overlay shows "Saved in N chunks".
- Confirm Gemma's `get_page_content` tool returns the full document, not just the first chunk.

## Migration

- **Phase 1 (ship chunking, no migration)**: new captures chunk; old single-drawer captures stay as-is and read as `chunk_count=1`. Users who recapture an old page replace its drawer naturally.
- **Phase 2 (optional backfill, future spec)**: CLI command `memorymark rechunk` iterates existing drawers, re-chunks those exceeding a threshold, and upserts with the new schema. Out of scope here.

## Error Handling

| Scenario                                         | Status | Response                                                                 |
|--------------------------------------------------|--------|--------------------------------------------------------------------------|
| Document > `DOCUMENT_MAX_BYTES` (5 MB)           | 413    | `{"detail": "document exceeds maximum size of 5242880 bytes"}`           |
| Chunk upsert fails mid-document                  | 500    | `{"detail": "...", "partial_drawer_ids": [...]}` after rollback          |
| Source URL missing                               | 400    | Same as today; `source_url` is required for chunked docs to enable by-url join |
| Delete on a single chunk of a multi-chunk doc    | 409    | `{"detail": "...", "document_id": "..."}`                                 |
| Assemble on a document with missing chunk indices | 500   | `{"detail": "document corrupt: chunks [N, M, ...] missing"}`             |

The extension surfaces all of these via the existing `bridgeErrorMessage` helper → overlay `error` state (`data.error` is rendered in `content.js:57`).

## Performance Considerations

- Chunk count for a 100 000-char doc with 4 000-char target ≈ 27 chunks. ChromaDB `upsert` handles batch writes natively — a single call with 27 ids/documents/metadatas is O(1) network round trips to the embedder.
- Embedder cost scales with total token count, which is unchanged vs. today (same content, different batching). No net slowdown expected.
- SSE event coalescing prevents N listener wake-ups for one document capture.

## Critical Constraints (additional)

- The chunker must be a pure function — no I/O, no global state — so it is trivially unit-testable and deterministic.
- Chunking must not reflow or re-format Markdown — overlap regions are copied verbatim; splits never mutate characters.
- The 5 MB document ceiling is a defense against runaway extractions (e.g. a content-script bug that captures `document.documentElement.outerHTML` of a SPA). It is not the real retrieval-quality ceiling — just an upper guardrail.

## Out of Scope

- Chunk-level re-ranking or semantic fusion in search (return top-N chunks from distinct documents is the limit here).
- Any change to MemPalace's stdio MCP server or `tool_add_drawer` contract.
- UI for browsing chunks individually (users interact with documents, not chunks).
- Chunking for non-web content (diary, agent writes via `POST /api/drawers`) — agents that want chunking can call `POST /api/documents`.
- Cross-document retrieval improvements (reranking, HyDE, MMR) — separate spec.

## Open Questions

1. Do we want `chunk_count` exposed as a top-level SSE event field, or buried in `data`? Depends on how the future capture-history popup consumes it.
2. Should `/api/search` `group=chunk` also expose inter-chunk scoring context (e.g. adjacent-chunk similarity) for callers that want to expand hits into larger passages? Could be a `window` param in a follow-up.
3. How should the KG integration (separate spec) attribute triples when extracted from a chunked document — per-chunk or per-document `source_drawer_id`? Resolve when both specs land.
