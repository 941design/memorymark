# Passage Index: full-text semantic recall for long articles

Status: IMPLEMENTED (supersedes `document-chunking-spec.md`, which is deleted).
Plan reviewed by Fable before implementation; review findings are folded in
below (marked *review*).

## Problem

Every drawer in the palace is embedded as one vector by ChromaDB's default
model (all-MiniLM-L6-v2). That model reads at most **256 tokens** (about
1 000–1 300 characters of English) and silently truncates the rest. In the
live palace (53 856 drawers, ~328 M characters) 98 % of drawers exceed that
window. Search therefore only "sees" each article's opening; passages further
in are unreachable by semantic search, although the full text is stored and
readable.

MemPalace itself assumes small drawers: its own miners chunk at 800 chars.
Our writers (extension, news-monitor, daydreamer, …) store whole articles as
single drawers via `POST /api/drawers` and MCP `mempalace_add_drawer`.

## Decisions (agreed with the user)

1. Chunking applies to **all writers, transparently**. External writers
   (news-monitor, daydreamer — code not in this repo) need no changes.
2. Counts (status/wings/rooms/taxonomy) report **articles**, not chunks.
3. Search returns **one hit per article** (best passage + article id).
4. The migration must be **lossless; all articles must be preserved**.

## Design: articles stay; a derived passage index is added

The key choice: **do not rewrite the article store.** The `mempalace_drawers`
collection keeps one drawer per article, byte-for-byte as today. Next to it the
bridge maintains a **passage index**: a separate ChromaDB store whose entries are
token-bounded chunks of each drawer, each pointing back to its parent drawer.

Consequences:

- **Lossless by construction.** Migration only *reads* articles and *adds* a
  new store. Nothing in `mempalace_drawers` is modified or deleted. Rollback =
  delete the index directory.
- **No id churn.** `drawer_id` stays the article id everywhere (REST, MCP,
  SSE, extension history, KG references, external writers' stored ids).
- **Counts are already per article.** MemPalace's status/list_wings/
  list_rooms/taxonomy/list_drawers/get/delete/traverse keep working unchanged.
- **Rebuildable.** The index is derived data; chunking parameters can change
  later with a re-index, never a data migration.
- Only search (REST + MCP) and index sync on writes are new behaviour.

Rejected alternatives: replacing drawers with chunk drawers (rewrites 54 k
articles, needs exact reassembly for losslessness, changes every id, inflates
MemPalace counts and its 5 s metadata cache scans to ~400 k rows); switching to
a long-context embedding model (still truncates long articles, blurs passages,
requires patching every MemPalace collection opener, 6–25× slower model).

### Storage

- Location: `${MEMORYMARK_INDEX_PATH}`, default `<palace_path>/memorymark-passages/`
  (inside the palace dir, so the Docker bind mount and backups cover it).
  A separate `chromadb.PersistentClient` → separate `chroma.sqlite3`, so index
  writes never touch the article database (and never trip MemPalace's
  inode/mtime reconnect logic).
- Collection `memorymark_passages`, `hnsw:space=cosine`, default embedding
  function (same model as the palace, so query embeddings are comparable).
- Passage id: `{drawer_id}::p{index:05d}`.
- Passage text is **not stored** (*review*): only the embedding and char
  offsets. Search slices `article[start:end]` from the article it fetches
  anyway. Halves disk use and removes a stale-copy failure class.
- State table `state.sqlite3` in the index dir (*review*): per drawer the
  content hash, wing, room, passage count, chunker version. Written *after*
  passages on (re)index, removed *before* them, so a crash leaves a detectable
  state. Start-up reconcile compares ids/wing/room/version against it in
  O(articles) without reading any article text; `CHUNKER_VERSION` bumps force a
  rebuild.
- Passage metadata: `drawer_id`, `wing`, `room`, `chunk_index`, `chunk_count`,
  `start`, `end` (char offsets into the article), `content_hash`
  (sha256 of the full article). Display metadata (title, url, …) is **not**
  copied; search fetches it from the article drawer, so it can never go stale.

### Chunker (`bridge/passages.py`, pure function)

`chunk_text(text, count_tokens/encode, max_tokens=230, overlap_tokens=32) -> list[Passage(start, end)]`

- Token-aware, using a **private** instance of the embedding model's tokenizer
  with truncation and padding disabled (*review*: `tokenizer.json` truncates at
  128, and Chroma's cached instance pads/truncates at 256 — mutating that
  shared instance breaks embedding), so
  every passage fits the 256-token window (230 + [CLS]/[SEP] + margin).
  Measured on 300 live articles: fixed 800-char windows overflow 256 tokens in
  18 % of cases (code, URLs, German); token-bounded windows never do.
- Window end chosen at the best boundary inside the last ~35 % of the window,
  preference: blank line > newline > sentence end (`.!?` + space) > whitespace
  > any token boundary.
- Consecutive passages overlap by ~`overlap_tokens`.
- Passages are verbatim slices (`text[start:end]`) — never reformatted.
- Whitespace-only passages are dropped.
- Invariants (tested): deterministic; every non-whitespace character of the
  article lies inside at least one passage; every passage ≤ max_tokens tokens;
  `start` strictly increasing; short text → exactly one passage.
- Passage ranges tile the article: a passage extends to the start of the
  next token, the first starts at 0 and the last ends at `len(article)`.
- No char-window fallback: the tokenizer ships with the embedding model, so if
  it is missing nothing can be embedded either.
- Tests re-tokenize every slice and assert `tokens + 2 ≤ 256` (*review*).

### Index operations (`PassageIndex`)

- `sync(drawer_id)` — **id-driven** (*review*: avoids reconcile/inline
  TOCTOU races): reads the article itself; compares with the state row;
  unchanged → no-op; wing/room only → passage metadata updated in place, no
  re-embedding (*review*); otherwise chunk + embed **outside** the lock
  (*review*), then under the lock re-read the article and only write if its
  hash is still the one embedded (else retry). A missing article → its
  passages and state row are removed. Idempotent.
- `reconcile(full=False)` — emits candidate ids only (missing/stale state
  rows, wing/room drift, old chunker version; with `full` also content-hash
  mismatch) plus orphan state rows, and calls `sync(id)` for each, so every
  decision is re-checked against the live article. Resumable: completed
  articles are skipped next time.
- `search(query, wing, room, limit, max_distance)` — query passages with
  `n_results = min(limit * 8, 400)`, group by `drawer_id` keeping the best
  distance (and counting matched passages), widen once (×4, cap 1 000) if
  fewer than `limit` distinct articles were found, take the top `limit`
  articles, fetch their documents/metadata from the article collection in one
  `get`. Articles that vanished meanwhile are skipped.
- `stats()` — passages, indexed articles.
- A process-wide lock serialises index mutations (REST thread pool, MCP thread,
  background reconcile).

### Keeping the index in sync

- **REST writes** (`POST/PUT/DELETE /api/drawers`, `POST /api/diary/write` —
  *review*: the latter two call MemPalace functions directly, bypassing the MCP
  wrapper): after the article write
  succeeds, `sync` inline. Index failures never fail the write (the article is
  already stored); they are logged and the response carries
  `"indexed": false`; the next reconcile repairs it.
- **MCP writes**: wrap the `handler`s of `mempalace_add_drawer`,
  `mempalace_update_drawer`, `mempalace_delete_drawer`, `mempalace_diary_write`
  in `mempalace.mcp_server.TOOLS` with `functools.wraps` (*review*: a bare
  `**kwargs` wrapper would make `handle_request` skip its argument whitelist);
  after a successful call, sync the affected id.
- **Out-of-band writers** (MemPalace CLI `mine`, scripts writing the palace
  directly): reconciled by the background reconcile at bridge start-up and by
  `make reindex`.
- **Start-up**: the lifespan starts a background reconcile thread (disable with
  `MEMORYMARK_RECONCILE_ON_START=0`; tests disable it). Progress is visible in
  `GET /api/status` → `passage_index`.

### Search behaviour

REST `GET /api/search` (shape stays backward compatible):

```json
{
  "query": "...", "filters": {"wing": null, "room": null},
  "results": [{
    "drawer_id": "drawer_News_arxiv-ai_…",      // article id, as before
    "content": "…full article…",                 // as before; omitted when include_content=false
    "passage": "…best-matching passage…",        // new
    "passage_start": 41210, "passage_end": 42188, // new: char offsets into content
    "matched_passages": 3,                        // new
    "distance": 0.41,                             // best passage distance
    "metadata": {"source_url": "…", "title": "…", "timestamp": "…"},
    "wing": "News", "room": "arxiv-ai", "added_by": "news-monitor"
  }],
  "index": "passages"                             // or "articles" when falling back
}
```

New query param `include_content` (default `true` for compatibility).
Fallback: if the passage index is empty (fresh install before the first
reconcile finished), search the article collection as today and report
`"index": "articles"`. While a first build is in progress, not-yet-indexed
articles are not found; `GET /api/status` → `passage_index.coverage` shows
progress (*review* S2 — accepted as-is: the live index is built before the
new bridge starts, so in practice only the start-up delta is ever pending).

MCP `mempalace_search`: same grouped search. Result items keep MemPalace's keys
(`text`, `wing`, `room`, `source_file`, `similarity`, `distance`) with `text` =
the best passage, plus `drawer_id`, `title`, `source_url`, `matched_passages`.
Tool description updated: "one hit per drawer; use mempalace_get_drawer for the
full text". MemPalace's query sanitiser (`sanitize_query`) is still applied.

Unchanged: `mempalace_check_duplicate` (article-level vectors are fine for
duplicate detection), KG, diary read, graph traversal, SSE events (index writes
are not WAL operations).

### Extension

- `offscreen.js` chat context uses `r.passage` (fallback `r.content`) instead of
  the article's first 1 500 chars; requests `include_content=false`.
- `background.js` `/search` preview uses `r.passage`.
- Capture path unchanged (still `POST /api/drawers`, 200 000-char cap).

## Migration (= first full index build) — lossless procedure

The article store is never written. Procedure, also scripted as
`make reindex` / `bridge/reindex.py`:

0. Refuses to run unless chromadb is the pinned version (*review*: another
   version could migrate the palace schema on open). Opens the article
   collection with `get_collection`, never `get_or_create_collection`.
1. **Backup**: copy the palace directory (minus the index dir) to
   `<palace>/../backups/palace-<timestamp>/` (`--no-backup` to skip). Writers
   must be stopped for the copy (*review*: a SQLite file copied under write may
   be torn).
2. **Manifest before**: sha256 over every article `(id, document,
   canonical-JSON metadata)`.
3. **Build**: `reconcile` into the index path (resumable; progress + ETA).
4. **Verify**:
   a. Every article of the before-manifest is still present and
      byte-identical; additions are allowed (*review*: writers may add
      articles during an hours-long build).
   b. Every article has exactly `chunk_count` passages with its current hash.
   c. Coverage: for every article, passages `0..n-1` tile `[0, len)` —
      the first starts at 0, the last ends at `len(article)`, each starts
      inside or at the end of its predecessor; plus a vector-segment smoke
      query.
   d. No orphan passages or state rows.
   Reconcile ends with up to three cheap catch-up passes so articles added
   during the run are indexed too; edits/deletes by writers during the run
   are reported (rerun with writers stopped). `--verify` never creates
   anything (no index → exit 1). Exit codes: 0 verified, 1 failed, 2 setup.
   Exit non-zero on any failure.

Live run (expected ~5 h at ~28 passages/s on the 4-vCPU VM, ~530 k
passages measured at ~9 per article): with the mac bridge stopped, snapshot
the palace to the VM's local disk (also serves as the backup); restart the old
bridge; build the index against the local copy (no SQLite writes over the
shared mount; same chromadb version and arch, so the HNSW files are portable);
verify; then with the mac bridge stopped again copy the finished
`memorymark-passages/` into the live palace and run `reindex.py --no-backup`
against it, which indexes the delta written since the snapshot and verifies
everything. Then the bridge is restarted with the new code. (*review* S1
suggested building on the mac instead; the user chose that the migration is
run from this session.)

## Tests

- `test_passages.py`: chunker invariants (determinism, coverage, token bound
  by re-tokenizing, boundary preference, short text, whitespace-only,
  code/URL/unicode-heavy text, untruncated tokenizer); index sync idempotency,
  update re-index, wing/room move without re-embedding, delete, diary, index
  failure not failing the write, stale-sync race, out-of-band writes, orphan
  removal, partial-write repair, chunker-version bump; search; migration.
  Also in `test_passages.py`: search (a term only present at the end of a
  long article is found; one hit per article; `include_content=false`;
  filters; fallback when the index is empty; deleted articles skipped),
  status, and migration (`reindex.main` on a seeded legacy palace leaves the
  article manifest identical and verifies; verify detects corrupted passages
  and orphans; missing palace → 2; verify without index creates nothing).
- `test_mcp.py`: MCP add/delete sync the index; MCP search is grouped and
  returns passages; wrappers keep the argument whitelist; status counts
  articles.

Resource notes: ~530 k passages × 384 floats ≈ 0.8 GB of vectors plus HNSW
graph on disk; Chroma keeps the HNSW index resident in the bridge process.

## Docs

`docs/rest-api.md` (search, status, write responses, reindex),
`docs/mcp-integration.md` (search semantics), `README.md` (architecture note,
`make reindex`, `MEMORYMARK_INDEX_PATH`), `specs/spec.md` pointer.

## Out of scope

Keyword/hybrid search (BM25), re-ranking, contextual chunk headers (title
prefixed to each passage embedding), changing the embedding model,
`check_duplicate` on passages.
