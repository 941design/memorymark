"""Passage index: token-bounded chunks of every drawer, for full-text semantic recall.

MemPalace embeds each drawer as one vector, and its default embedding model
(all-MiniLM-L6-v2) only reads the first 256 tokens. Drawers written through the
bridge are whole articles, so search used to "see" only their opening.

This module keeps the article store untouched and maintains a *derived* index
next to it (see specs/passage-index-spec.md):

- a separate ChromaDB store (its own chroma.sqlite3) holding one embedding per
  passage, with the passage's char offsets into its parent drawer — passage
  text is never stored, it is sliced from the article on read;
- a small SQLite state table recording, per drawer, the article hash, wing,
  room, passage count and chunker version the passages were built from.

The index is rebuildable from the article store at any time; deleting the
index directory is a complete rollback.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

logger = logging.getLogger("memorymark.passages")

# Bump whenever chunking output for the same input can change; every drawer
# indexed with an older version is re-indexed by the next reconcile.
CHUNKER_VERSION = 1

# all-MiniLM-L6-v2 reads 256 tokens including [CLS] and [SEP]. Re-tokenizing a
# slice can yield one token more than the window it was cut from, so leave a
# margin.
MODEL_MAX_TOKENS = 256
MAX_TOKENS = 230
OVERLAP_TOKENS = 32
# A window may end early (at a better boundary) but not before this fraction.
MIN_WINDOW_FRACTION = 0.65

COLLECTION_NAME = "memorymark_passages"
INDEX_DIRNAME = "memorymark-passages"
STATE_DB = "state.sqlite3"
_ADD_BATCH = 2000  # passages per collection.add call (Chroma max is ~5 000)
_EMBED_BATCH = 256

_SENTENCE_END = (".", "!", "?", ":", ";")


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class Tokenizer:
    """Private instance of the embedding model's tokenizer.

    The ``tokenizer.json`` shipped with the model has truncation baked in, and
    Chroma's embedding function enables truncation + padding on its own cached
    instance. Mutating that shared instance breaks embedding, so we load our
    own copy and disable both.
    """

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
        from tokenizers import Tokenizer as HFTokenizer

        ef = ONNXMiniLM_L6_V2()
        ef._download_model_if_not_exists()
        path = os.path.join(ef.DOWNLOAD_PATH, ef.EXTRACTED_FOLDER_NAME, "tokenizer.json")
        tok = HFTokenizer.from_file(path)
        tok.no_truncation()
        tok.no_padding()
        self._tok = tok

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Char spans of the content tokens of ``text`` (special tokens excluded)."""
        enc = self._tok.encode(text, add_special_tokens=False)
        return [(s, e) for (s, e) in enc.offsets if e > s]

    def count(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False).ids)


# ---------------------------------------------------------------------------
# Chunker (pure)
# ---------------------------------------------------------------------------


def _boundary_score(text: str, spans: list[tuple[int, int]], b: int) -> int:
    """How good it is to end a passage before token ``b`` (higher = better)."""
    gap = text[spans[b - 1][1] : spans[b][0]]
    if "\n\n" in gap or "\n\r\n" in gap:
        return 4
    if "\n" in gap:
        return 3
    if gap and text[spans[b - 1][1] - 1] in _SENTENCE_END:
        return 2
    if gap:
        return 1
    return 0


def chunk_spans(
    text: str,
    spans: list[tuple[int, int]],
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[tuple[int, int]]:
    """Split ``text`` into passages given its token ``spans``.

    Returns ``(start, end)`` char ranges. Guarantees:
    - deterministic;
    - the ranges cover ``[0, len(text))`` completely (consecutive passages
      overlap or touch), so no character is outside every passage;
    - each passage holds at most ``max_tokens`` tokens of the original
      tokenization;
    - starts are strictly increasing.
    Text without any token (empty / whitespace only) yields no passages.
    """
    n = len(spans)
    if n == 0:
        return []
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    def char_start(a: int) -> int:
        return 0 if a == 0 else spans[a][0]

    def char_end(b: int) -> int:
        # Include the gap after the last token so ranges tile the text.
        return len(text) if b >= n else spans[b][0]

    out: list[tuple[int, int]] = []
    a = 0
    while True:
        b_max = min(a + max_tokens, n)
        if b_max >= n:
            out.append((char_start(a), char_end(n)))
            return out
        b_min = a + max(1, int(max_tokens * MIN_WINDOW_FRACTION))
        best_b, best_score = b_max, -1
        for b in range(b_max, b_min - 1, -1):
            score = _boundary_score(text, spans, b)
            if score > best_score:
                best_b, best_score = b, score
                if score == 4:
                    break
        out.append((char_start(a), char_end(best_b)))
        # Next window starts overlap_tokens back, snapped forward to a word start.
        nxt = max(best_b - overlap_tokens, a + 1)
        for c in range(nxt, best_b):
            if spans[c][0] > spans[c - 1][1]:
                nxt = c
                break
        a = nxt


_SHARED_TOKENIZER: Tokenizer | None = None
_SHARED_EF = None
_SHARED_LOCK = threading.Lock()


def shared_tokenizer() -> Tokenizer:
    global _SHARED_TOKENIZER
    with _SHARED_LOCK:
        if _SHARED_TOKENIZER is None:
            _SHARED_TOKENIZER = Tokenizer()
        return _SHARED_TOKENIZER


def _shared_ef():
    """Same model and settings Chroma uses for the article collection."""
    global _SHARED_EF
    with _SHARED_LOCK:
        if _SHARED_EF is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            _SHARED_EF = DefaultEmbeddingFunction()
        return _SHARED_EF


def chunk_text(text: str, tokenizer: Tokenizer, **kw) -> list[tuple[int, int]]:
    return chunk_spans(text, tokenizer.spans(text), **kw)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@dataclass
class ReconcileStatus:
    state: str = "idle"  # idle | running | done | error
    mode: str = ""
    total: int = 0
    processed: int = 0
    indexed: int = 0
    removed: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "failures"}
        d["failures"] = self.failures[:20]
        return d


ArticleGetter = Callable[[], object]


class PassageIndex:
    """Derived passage index for the drawers of one palace."""

    def __init__(self, path: str, articles: ArticleGetter, *, tokenizer: Tokenizer | None = None,
                 embedding_function=None) -> None:
        import chromadb

        os.makedirs(path, exist_ok=True)
        self.path = path
        self._articles = articles
        self._tokenizer = tokenizer
        self._ef = embedding_function or _shared_ef()
        self._client = chromadb.PersistentClient(path=path)
        self._col = self._client.get_or_create_collection(
            COLLECTION_NAME, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        self._lock = threading.RLock()
        self._db = sqlite3.connect(os.path.join(path, STATE_DB), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS articles (
                   drawer_id TEXT PRIMARY KEY,
                   content_hash TEXT NOT NULL,
                   wing TEXT NOT NULL,
                   room TEXT NOT NULL,
                   chunk_count INTEGER NOT NULL,
                   chunker_version INTEGER NOT NULL,
                   indexed_at REAL NOT NULL)"""
        )
        self._db.commit()
        self.reconcile_status = ReconcileStatus()

    # -- helpers -----------------------------------------------------------

    @property
    def tokenizer(self) -> Tokenizer:
        if self._tokenizer is None:
            self._tokenizer = shared_tokenizer()
        return self._tokenizer

    def _article_col(self):
        col = self._articles()
        if col is None:
            raise RuntimeError("No palace found")
        return col

    def _get_article(self, drawer_id: str) -> tuple[str, dict] | None:
        res = self._article_col().get(ids=[drawer_id], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        doc = (res.get("documents") or [None])[0]
        meta = (res.get("metadatas") or [None])[0] or {}
        if doc is None:
            doc = ""
        return doc, meta

    def _state(self, drawer_id: str) -> tuple | None:
        with self._lock:
            return self._db.execute(
                "SELECT content_hash, wing, room, chunk_count, chunker_version FROM articles WHERE drawer_id=?",
                (drawer_id,),
            ).fetchone()

    def embed(self, texts: list[str]) -> list:
        out: list = []
        for i in range(0, len(texts), _EMBED_BATCH):
            out.extend(self._ef(texts[i : i + _EMBED_BATCH]))
        return [list(map(float, v)) for v in out]

    def _delete_passages(self, drawer_id: str) -> None:
        self._col.delete(where={"drawer_id": drawer_id})

    # -- sync --------------------------------------------------------------

    def sync(self, drawer_id: str, attempts: int = 4) -> str:
        """Bring the passages of ``drawer_id`` in line with the article store.

        Id-driven: the article is (re-)read here, and re-checked under the lock
        before anything is written, so a stale caller can never resurrect a
        deleted article's passages or index outdated content. Chunking and
        embedding happen outside the lock; if the article changed meanwhile the
        attempt is discarded and retried (again outside the lock). Returns one
        of: unchanged, indexed, moved, deleted.
        """
        for _ in range(attempts):
            result = self._sync_once(drawer_id)
            if result is not None:
                return result
        raise RuntimeError(f"article {drawer_id} kept changing during indexing")

    def _sync_once(self, drawer_id: str) -> str | None:
        """One sync attempt; None means "article changed underneath, retry"."""
        art = self._get_article(drawer_id)
        if art is None:
            with self._lock:
                if self._get_article(drawer_id) is not None:
                    return None
                # Passages first, state row last: a crash in between leaves a
                # state row for a missing article, which reconcile retries.
                self._delete_passages(drawer_id)
                self._db.execute("DELETE FROM articles WHERE drawer_id=?", (drawer_id,))
                self._db.commit()
            return "deleted"

        doc, meta = art
        h = content_hash(doc)
        wing, room = meta.get("wing", ""), meta.get("room", "")
        st = self._state(drawer_id)
        if st and st[0] == h and st[4] == CHUNKER_VERSION:
            if (st[1], st[2]) == (wing, room):
                return "unchanged"
            with self._lock:
                cur = self._get_article(drawer_id)
                if cur is None or content_hash(cur[0]) != h:
                    return None
                wing, room = cur[1].get("wing", ""), cur[1].get("room", "")
                got = self._col.get(where={"drawer_id": drawer_id}, include=["metadatas"])
                if got["ids"]:
                    metas = [dict(m, wing=wing, room=room) for m in got["metadatas"]]
                    self._col.update(ids=got["ids"], metadatas=metas)
                self._db.execute(
                    "UPDATE articles SET wing=?, room=?, indexed_at=? WHERE drawer_id=?",
                    (wing, room, time.time(), drawer_id),
                )
                self._db.commit()
            return "moved"

        ranges = chunk_text(doc, self.tokenizer)
        embeddings = self.embed([doc[s:e] for s, e in ranges]) if ranges else []

        with self._lock:
            cur = self._get_article(drawer_id)
            if cur is None or content_hash(cur[0]) != h:
                return None  # changed or vanished while we were embedding
            wing, room = cur[1].get("wing", ""), cur[1].get("room", "")
            # State row first: a crash mid-write leaves passages without a
            # state row, which reconcile treats as unindexed and rebuilds.
            self._db.execute("DELETE FROM articles WHERE drawer_id=?", (drawer_id,))
            self._db.commit()
            self._delete_passages(drawer_id)
            count = len(ranges)
            ids = [f"{drawer_id}::p{i:05d}" for i in range(count)]
            metas = [
                {
                    "drawer_id": drawer_id,
                    "wing": wing,
                    "room": room,
                    "chunk_index": i,
                    "chunk_count": count,
                    "start": s,
                    "end": e,
                    "content_hash": h,
                }
                for i, (s, e) in enumerate(ranges)
            ]
            for i in range(0, count, _ADD_BATCH):
                self._col.add(
                    ids=ids[i : i + _ADD_BATCH],
                    embeddings=embeddings[i : i + _ADD_BATCH],
                    metadatas=metas[i : i + _ADD_BATCH],
                )
            self._db.execute(
                "INSERT INTO articles VALUES (?,?,?,?,?,?,?)",
                (drawer_id, h, wing, room, count, CHUNKER_VERSION, time.time()),
            )
            self._db.commit()
        return "indexed"

    # -- reconcile ---------------------------------------------------------

    def _iter_articles(self, include_documents: bool, page: int = 500) -> Iterable[tuple[str, str | None, dict]]:
        col = self._article_col()
        include = ["metadatas"] + (["documents"] if include_documents else [])
        offset = 0
        while True:
            batch = col.get(include=include, limit=page, offset=offset)
            ids = batch["ids"]
            if not ids:
                return
            docs = batch.get("documents") if include_documents else None
            for i, did in enumerate(ids):
                yield did, (docs[i] if docs else None), (batch["metadatas"][i] or {})
            offset += len(ids)

    def stale_ids(self, full: bool = False) -> tuple[list[str], list[str], int]:
        """Return (needs_sync, orphan_ids, article_count).

        Cheap mode compares ids, wing/room and chunker version against the
        state table. ``full`` also hashes every article's content (catches
        content edits made by writers that bypass the bridge).
        """
        with self._lock:
            state = {
                row[0]: row[1:]
                for row in self._db.execute(
                    "SELECT drawer_id, content_hash, wing, room, chunker_version FROM articles"
                ).fetchall()
            }
        needs: list[str] = []
        seen: set[str] = set()
        for did, doc, meta in self._iter_articles(include_documents=full):
            seen.add(did)
            st = state.get(did)
            if (
                st is None
                or st[3] != CHUNKER_VERSION
                or (st[1], st[2]) != (meta.get("wing", ""), meta.get("room", ""))
                or (full and st[0] != content_hash(doc or ""))
            ):
                needs.append(did)
        orphans = [did for did in state if did not in seen]
        if full:
            # Passages whose state row was lost (crash mid-write) and whose
            # article is gone are invisible to the state table; find them.
            known = set(state) | set(needs)
            orphans += sorted(self.passage_drawer_ids() - seen - known)
        return needs, orphans, len(seen)

    def passage_drawer_ids(self) -> set[str]:
        """Distinct parent drawer ids present in the passage collection (paged)."""
        out: set[str] = set()
        offset = 0
        while True:
            b = self._col.get(include=["metadatas"], limit=5000, offset=offset)
            if not b["ids"]:
                return out
            out.update(m.get("drawer_id", "") for m in b["metadatas"] if m)
            offset += len(b["ids"])

    def reconcile(self, full: bool = False, progress: Callable[[ReconcileStatus], None] | None = None,
                  catch_up_passes: int = 3) -> ReconcileStatus:
        """Sync every stale/missing/orphaned drawer.

        After the main pass, up to ``catch_up_passes`` cheap passes pick up
        drawers that writers added, moved or deleted during a long run.
        Drawers already handled in this run (including failures) are not
        retried within it.
        """
        rs = self.reconcile_status = ReconcileStatus(
            state="running", mode="full" if full else "quick", started_at=time.time()
        )
        done: set[str] = set()

        def process(todo: list[str]) -> None:
            rs.total += len(todo)
            if progress:
                progress(rs)
            for did in todo:
                try:
                    result = self.sync(did)
                    if result == "deleted":
                        rs.removed += 1
                    elif result != "unchanged":
                        rs.indexed += 1
                except Exception as e:  # keep going; report at the end
                    logger.exception("indexing %s failed", did)
                    rs.failed += 1
                    rs.failures.append(f"{did}: {e}")
                done.add(did)
                rs.processed += 1
                if progress:
                    progress(rs)

        try:
            needs, orphans, _ = self.stale_ids(full=full)
            process(needs + orphans)
            for _ in range(catch_up_passes):
                needs, orphans, _ = self.stale_ids(full=False)
                todo = [d for d in needs + orphans if d not in done]
                if not todo:
                    break
                process(todo)
            rs.state = "done"
        except Exception as e:
            logger.exception("reconcile failed")
            rs.state, rs.error = "error", str(e)
        rs.finished_at = time.time()
        if progress:
            progress(rs)
        return rs

    # -- read --------------------------------------------------------------

    def passage_count(self) -> int:
        return self._col.count()

    def indexed_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def stats(self) -> dict:
        return {
            "path": self.path,
            "passages": self.passage_count(),
            "indexed_articles": self.indexed_count(),
            "chunker_version": CHUNKER_VERSION,
            "reconcile": self.reconcile_status.as_dict(),
        }

    def search(self, query: str, *, where: dict | None, limit: int, max_distance: float | None = None) -> list[dict]:
        """Best passage per article, ordered by distance.

        Returns dicts with drawer_id, document, metadata (article), distance,
        passage_start, passage_end, matched_passages. Articles whose passages
        are stale (hash mismatch) fall back to their opening as the passage.
        """
        total = self._col.count()
        if total == 0:
            return []
        qemb = self.embed([query])
        best: dict[str, dict] = {}
        n = min(limit * 8, 400)
        while True:
            kwargs = {
                "query_embeddings": qemb,
                "n_results": min(n, total),
                "include": ["metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where
            res = self._col.query(**kwargs)
            best = {}
            got = 0
            for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
                got += 1
                if max_distance is not None and dist > max_distance:
                    continue
                did = meta["drawer_id"]
                cur = best.get(did)
                if cur is None:
                    best[did] = {"meta": meta, "distance": dist, "matched": 1}
                else:
                    cur["matched"] += 1
                    if dist < cur["distance"]:
                        cur["meta"], cur["distance"] = meta, dist
            dists = res["distances"][0]
            beyond = max_distance is not None and dists and dists[-1] > max_distance
            if len(best) >= limit or beyond or got < kwargs["n_results"] or n >= 1000 or n >= total:
                break
            n = min(n * 4, 1000)

        top = sorted(best.items(), key=lambda kv: kv[1]["distance"])[:limit]
        if not top:
            return []
        arts = self._article_col().get(ids=[d for d, _ in top], include=["documents", "metadatas"])
        by_id = {
            did: ((arts["documents"] or [None] * len(arts["ids"]))[i] or "", (arts["metadatas"][i] or {}))
            for i, did in enumerate(arts["ids"])
        }
        hits = []
        for did, info in top:
            if did not in by_id:
                continue  # article deleted since indexing
            doc, ameta = by_id[did]
            pm = info["meta"]
            start, end = int(pm.get("start", 0)), int(pm.get("end", 0))
            if pm.get("content_hash") != content_hash(doc):
                start, end = 0, min(len(doc), 1000)
            hits.append(
                {
                    "drawer_id": did,
                    "document": doc,
                    "metadata": ameta,
                    "distance": info["distance"],
                    "passage_start": start,
                    "passage_end": end,
                    "matched_passages": info["matched"],
                }
            )
        return hits

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
        self._col = None
        self._client = None


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_INDEX: PassageIndex | None = None
_INDEX_LOCK = threading.Lock()


def default_index_path(palace_path: str) -> str:
    return os.environ.get("MEMORYMARK_INDEX_PATH") or os.path.join(palace_path, INDEX_DIRNAME)


_INDEX_KEY: tuple[str, str] | None = None


def get_index(palace_path: str, articles: ArticleGetter) -> PassageIndex:
    global _INDEX, _INDEX_KEY
    path = default_index_path(palace_path)
    key = (os.path.abspath(palace_path), os.path.abspath(path))
    with _INDEX_LOCK:
        if _INDEX is None or _INDEX_KEY != key:
            if _INDEX is not None:
                _INDEX.close()
            _INDEX = PassageIndex(path, articles)
            _INDEX_KEY = key
        return _INDEX


def reset_index() -> None:
    global _INDEX, _INDEX_KEY
    with _INDEX_LOCK:
        if _INDEX is not None:
            _INDEX.close()
        _INDEX = None
        _INDEX_KEY = None
