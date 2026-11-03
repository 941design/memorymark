"""Passage index: chunker invariants, index sync, reconcile, search, migration."""

import json
import random

import pytest

import passages
from passages import MODEL_MAX_TOKENS, chunk_spans, chunk_text, content_hash, shared_tokenizer


@pytest.fixture(scope="module")
def tok():
    return shared_tokenizer()


def _assert_invariants(text, ranges, tok, max_tokens=passages.MAX_TOKENS):
    if not text.strip():
        assert ranges == []
        return
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    for (a, b), (c, d) in zip(ranges, ranges[1:]):
        assert a < c <= b, "passages must overlap or touch, starts strictly increasing"
    for a, b in ranges:
        assert a < b
        # Re-tokenize the slice: what the embedder actually sees.
        assert tok.count(text[a:b]) + 2 <= MODEL_MAX_TOKENS


def _prose(n_paragraphs, seed=0):
    rnd = random.Random(seed)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda".split()
    paras = []
    for _ in range(n_paragraphs):
        sents = [" ".join(rnd.choice(words) for _ in range(rnd.randint(5, 20))).capitalize() + "."
                 for _ in range(rnd.randint(2, 6))]
        paras.append(" ".join(sents))
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def test_tokenizer_is_not_truncated(tok):
    """Regression (plan review B1): tokenizer.json ships with truncation; a
    truncating tokenizer would silently produce one passage per article."""
    assert tok.count("word " * 2000) == 2000


def test_short_text_is_one_passage(tok):
    text = "# Title\n\nA short note."
    assert chunk_text(text, tok) == [(0, len(text))]


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
def test_whitespace_only_text_has_no_passages(tok, text):
    assert chunk_text(text, tok) == []


def test_long_article_yields_many_passages(tok):
    text = _prose(80)
    assert len(text) > 5000
    ranges = chunk_text(text, tok)
    assert len(ranges) > 3
    _assert_invariants(text, ranges, tok)


@pytest.mark.parametrize(
    "text",
    [
        _prose(40, seed=1),
        # Every token boundary scores 0 and overlap snapping lands mid-word.
        # (A plain "x" * 20000 would be a single [UNK] token: WordPiece caps
        # words at 100 chars.)
        "ab,cd;" * 6000,
        "https://example.com/" + "/".join(f"seg{i}?q={i}&r=abc" for i in range(2000)),
        "def f(x):\n    return [i ** 2 for i in range(x) if i % 3 == 0]\n" * 300,
        "Über naïve Größenänderungen 🚀 — 日本語のテキスト。" * 400,
        "line\n" * 5000,
        "\n\n\n   leading whitespace\n\n" + _prose(20, seed=3) + "\n\n   \n",
    ],
    ids=["prose", "no-boundaries", "url", "code", "unicode", "lines", "padded"],
)
def test_chunker_invariants(tok, text):
    _assert_invariants(text, chunk_text(text, tok), tok)


def test_chunker_is_deterministic(tok):
    text = _prose(60, seed=7)
    assert chunk_text(text, tok) == chunk_text(text, tok)


def test_chunker_prefers_paragraph_boundaries(tok):
    text = _prose(60, seed=11)
    ranges = chunk_text(text, tok)
    ends = [b for _, b in ranges[:-1]]
    at_paragraph = sum(text[b - 2 : b] == "\n\n" or text[b:b + 2] == "\n\n" or text[b - 1 : b + 1] == "\n\n" for b in ends)
    assert at_paragraph >= len(ends) * 0.8


def test_overlap_must_be_smaller_than_window():
    with pytest.raises(ValueError):
        chunk_spans("a b c", [(0, 1), (2, 3), (4, 5)], max_tokens=2, overlap_tokens=2)


# ---------------------------------------------------------------------------
# Index sync via REST
# ---------------------------------------------------------------------------


def _index():
    import server
    return server._index()


def _passages_of(drawer_id):
    got = _index()._col.get(where={"drawer_id": drawer_id}, include=["metadatas"])
    return sorted(got["metadatas"], key=lambda m: m["chunk_index"])


def _create(client, content, wing="Web", room="Captures", url=None, title="T"):
    meta = {"title": title}
    if url:
        meta["source_url"] = url
    resp = client.post("/api/drawers", json={"wing": wing, "room": room, "content": content, "metadata": meta})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_indexes_all_passages(client):
    text = _prose(80)
    body = _create(client, text)
    assert body["indexed"] is True
    ps = _passages_of(body["drawer_id"])
    assert len(ps) > 3
    assert ps[0]["start"] == 0 and ps[-1]["end"] == len(text)
    assert all(p["content_hash"] == content_hash(text) and p["chunk_count"] == len(ps) for p in ps)


def test_create_is_idempotent_for_index(client):
    text = _prose(30)
    a = _create(client, text)
    n = _index().passage_count()
    b = _create(client, text)
    assert a["drawer_id"] == b["drawer_id"] and b["indexed"] is True
    assert _index().passage_count() == n


def test_update_content_reindexes(client):
    body = _create(client, _prose(30, seed=1))
    did = body["drawer_id"]
    new = _prose(60, seed=2)
    resp = client.put(f"/api/drawers/{did}", json={"content": new})
    assert resp.json()["indexed"] is True
    ps = _passages_of(did)
    assert ps[-1]["end"] == len(new) and ps[0]["content_hash"] == content_hash(new)


def test_update_wing_room_moves_passages_without_reembedding(client, monkeypatch):
    body = _create(client, _prose(30))
    did = body["drawer_id"]
    calls = []
    idx = _index()
    monkeypatch.setattr(idx, "embed", lambda texts: calls.append(len(texts)) or [])
    resp = client.put(f"/api/drawers/{did}", json={"wing": "Archive", "room": "Old"})
    assert resp.json()["indexed"] is True
    assert calls == []
    assert {(p["wing"], p["room"]) for p in _passages_of(did)} == {("Archive", "Old")}


def test_delete_removes_passages(client):
    did = _create(client, _prose(30))["drawer_id"]
    assert _passages_of(did)
    resp = client.delete(f"/api/drawers/{did}")
    assert resp.json() == {"success": True, "indexed": True}
    assert _passages_of(did) == []
    assert _index().indexed_count() == 0


def test_diary_write_is_indexed(client):
    resp = client.post("/api/diary/write", json={"agent_name": "tester", "entry": "Diary entry about zebras."})
    assert resp.status_code == 201
    body = resp.json()
    assert body["indexed"] is True
    assert _passages_of(body["entry_id"])


def test_index_failure_does_not_fail_write(client, monkeypatch):
    import server
    monkeypatch.setattr(server._index(), "sync", lambda did: (_ for _ in ()).throw(RuntimeError("boom")))
    body = _create(client, "content that will not be indexed")
    assert body["success"] is True and body["indexed"] is False
    # Article is stored regardless.
    assert client.get(f"/api/drawers/{body['drawer_id']}").status_code == 200


# ---------------------------------------------------------------------------
# Sync races and reconcile
# ---------------------------------------------------------------------------


def test_stale_sync_never_resurrects_deleted_article(client):
    """Plan review B3: sync is id-driven and re-reads the article under the lock."""
    did = _create(client, _prose(10))["drawer_id"]
    import server
    server._get_collection().delete(ids=[did])  # article vanishes behind our back
    assert _index().sync(did) == "deleted"
    assert _passages_of(did) == []


def test_reconcile_indexes_out_of_band_writes_and_removes_orphans(client):
    import server
    col = server._get_collection(create=True)
    kept = _create(client, _prose(20, seed=5))["drawer_id"]
    gone = _create(client, _prose(20, seed=6))["drawer_id"]
    # Out-of-band: a writer bypassing the bridge adds one article and deletes another.
    col.add(ids=["oob_1"], documents=[_prose(40, seed=9)], metadatas=[{"wing": "W", "room": "R"}])
    col.delete(ids=[gone])
    rs = _index().reconcile()
    assert rs.state == "done" and rs.failed == 0
    assert _passages_of("oob_1")
    assert _passages_of(gone) == []
    assert _passages_of(kept)
    assert _index().stale_ids(full=True)[:2] == ([], [])


def test_full_reconcile_detects_out_of_band_content_edit(client):
    import server
    did = _create(client, _prose(20, seed=1))["drawer_id"]
    new = _prose(50, seed=2)
    server._get_collection().update(ids=[did], documents=[new])
    assert did not in _index().stale_ids(full=False)[0]
    assert did in _index().stale_ids(full=True)[0]
    _index().reconcile(full=True)
    assert _passages_of(did)[-1]["end"] == len(new)


def test_reconcile_repairs_partial_write(client):
    """A crash between deleting the state row and writing it leaves passages
    without state; reconcile must rebuild that drawer."""
    did = _create(client, _prose(30))["drawer_id"]
    idx = _index()
    idx._db.execute("DELETE FROM articles WHERE drawer_id=?", (did,))
    idx._db.commit()
    some = idx._col.get(where={"drawer_id": did})["ids"][:2]
    idx._col.delete(ids=some)
    idx.reconcile()
    ps = _passages_of(did)
    assert [p["chunk_index"] for p in ps] == list(range(ps[0]["chunk_count"]))


def test_chunker_version_bump_triggers_reindex(client, monkeypatch):
    did = _create(client, _prose(10))["drawer_id"]
    monkeypatch.setattr(passages, "CHUNKER_VERSION", passages.CHUNKER_VERSION + 1)
    assert did in _index().stale_ids()[0]
    _index().reconcile()
    assert _index()._state(did)[4] == passages.CHUNKER_VERSION
    assert _index().stale_ids()[0] == []


def test_reconcile_catches_up_with_articles_added_during_run(client):
    """An article written after reconcile took its snapshot is still indexed
    (catch-up pass), so a long migration under live writers verifies."""
    import server
    col = server._get_collection(create=True)
    col.add(ids=["first"], documents=[_prose(10, seed=1)], metadatas=[{"wing": "W", "room": "R"}])
    idx = _index()
    original_sync = idx.sync
    added = []

    def sync_and_write(did, **kw):
        if not added:
            col.add(ids=["late"], documents=[_prose(10, seed=2)], metadatas=[{"wing": "W", "room": "R"}])
            added.append(True)
        return original_sync(did, **kw)

    idx.sync = sync_and_write
    try:
        rs = idx.reconcile()
    finally:
        del idx.sync
    assert rs.state == "done"
    assert _passages_of("late")


def test_full_reconcile_removes_passages_without_state_or_article(client):
    """Crash after the state row was removed, then the article was deleted
    out-of-band: the passages are invisible to the state table."""
    import server
    did = _create(client, _prose(20))["drawer_id"]
    idx = _index()
    idx._db.execute("DELETE FROM articles WHERE drawer_id=?", (did,))
    idx._db.commit()
    server._get_collection().delete(ids=[did])
    assert did not in idx.stale_ids(full=False)[1]
    assert did in idx.stale_ids(full=True)[1]
    idx.reconcile(full=True)
    assert _passages_of(did) == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


NEEDLE = "The quokka sanctuary on Rottnest Island reopened after the bushfire season."


def _long_with_needle_at_end():
    return _prose(120, seed=21) + "\n\n" + NEEDLE + "\n"


def test_search_finds_passage_deep_inside_long_article(client):
    text = _long_with_needle_at_end()
    did = _create(client, text, url="https://example.com/long")["drawer_id"]
    _create(client, _prose(5, seed=99), url="https://example.com/other")
    resp = client.get("/api/search", params={"query": "quokka sanctuary Rottnest Island", "limit": 1})
    body = resp.json()
    assert body["index"] == "passages"
    hit = body["results"][0]
    assert hit["drawer_id"] == did
    assert NEEDLE in hit["passage"]
    assert hit["passage"] == text[hit["passage_start"]:hit["passage_end"]]
    assert hit["content"] == text
    assert hit["passage_start"] > 5000
    assert hit["metadata"]["source_url"] == "https://example.com/long"


def test_search_returns_one_hit_per_article(client):
    _create(client, "\n\n".join(["Zebras are striped animals of Africa."] * 3 + [_prose(60)]))
    _create(client, "Zebras graze in the savanna.")
    resp = client.get("/api/search", params={"query": "zebra", "limit": 5})
    ids = [r["drawer_id"] for r in resp.json()["results"]]
    assert len(ids) == len(set(ids)) == 2


def test_search_include_content_false(client):
    _create(client, _long_with_needle_at_end())
    hit = client.get("/api/search", params={"query": "quokka", "include_content": "false"}).json()["results"][0]
    assert "content" not in hit and NEEDLE in hit["passage"]


def test_search_wing_filter_applies_to_passages(client):
    _create(client, "Penguins live in Antarctica.", wing="A", room="r")
    _create(client, "Penguins also live in South Africa.", wing="B", room="r")
    res = client.get("/api/search", params={"query": "penguins", "wing": "B"}).json()["results"]
    assert [r["wing"] for r in res] == ["B"]


def test_search_falls_back_to_articles_when_index_empty(client):
    import server
    col = server._get_collection(create=True)
    col.add(ids=["legacy_1"], documents=["Legacy article about lighthouses."], metadatas=[{"wing": "W", "room": "R"}])
    body = client.get("/api/search", params={"query": "lighthouse"}).json()
    assert body["index"] == "articles"
    assert body["results"][0]["drawer_id"] == "legacy_1"
    assert "lighthouses" in body["results"][0]["passage"]


def test_search_skips_articles_deleted_after_indexing(client):
    import server
    did = _create(client, "Article about walruses.")["drawer_id"]
    server._get_collection().delete(ids=[did])  # passages still present
    res = client.get("/api/search", params={"query": "walrus"}).json()["results"]
    assert did not in [r["drawer_id"] for r in res]


def test_status_reports_passage_index(client):
    _create(client, _prose(30))
    pi = client.get("/api/status").json()["passage_index"]
    assert pi["indexed_articles"] == 1 and pi["passages"] > 1 and pi["coverage"] == 1.0
    assert pi["chunker_version"] == passages.CHUNKER_VERSION


# ---------------------------------------------------------------------------
# Migration (reindex.py)
# ---------------------------------------------------------------------------


def _seed_legacy_palace():
    """Articles written the pre-index way (directly into the collection)."""
    import server
    col = server._get_collection(create=True)
    docs = {f"legacy_{i}": _prose(10 + i * 7, seed=i) for i in range(6)}
    docs["legacy_empty_ws"] = "   "
    docs["legacy_unicode"] = "Größe 🚀 " * 500
    col.add(
        ids=list(docs),
        documents=list(docs.values()),
        metadatas=[{"wing": "W", "room": "R", "title": k, "chunk_index": 0} for k in docs],
    )
    return col, docs


def test_reindex_builds_verifies_and_preserves_articles(isolated_palace, tmp_path, capsys):
    import reindex
    col, docs = _seed_legacy_palace()
    before = reindex.manifest(col)
    rc = reindex.main(["--palace", isolated_palace, "--backup-dir", str(tmp_path / "bk")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "article store intact" in out and "index verified" in out
    assert reindex.manifest(col) == before
    # Backup exists and excludes the index.
    (bk,) = list((tmp_path / "bk").iterdir())
    assert (bk / "chroma.sqlite3").exists()
    assert not (bk / passages.INDEX_DIRNAME).exists()
    # Re-running is a no-op and still verifies.
    assert reindex.main(["--palace", isolated_palace, "--no-backup"]) == 0


def test_reindex_verify_detects_problems(isolated_palace, capsys):
    import reindex
    col, docs = _seed_legacy_palace()
    assert reindex.main(["--palace", isolated_palace, "--no-backup"]) == 0
    idx = passages.PassageIndex(passages.default_index_path(isolated_palace), lambda: col)
    victim = "legacy_5"
    pid = idx._col.get(where={"drawer_id": victim})["ids"][1]
    idx._col.update(ids=[pid], metadatas=[{"drawer_id": victim, "chunk_index": 1, "start": 999999, "end": 1000000,
                                           "content_hash": content_hash(docs[victim]), "wing": "W", "room": "R",
                                           "chunk_count": 2}])
    idx._col.add(ids=["ghost::p00000"], embeddings=[[0.0] * 384],
                 metadatas=[{"drawer_id": "ghost", "chunk_index": 0, "start": 0, "end": 1, "content_hash": "x"}])
    idx.close()
    capsys.readouterr()
    assert reindex.main(["--palace", isolated_palace, "--verify"]) == 1
    out = capsys.readouterr().out
    assert "ghost: orphan passages" in out
    assert victim in out


def test_reindex_refuses_missing_palace(tmp_path):
    import reindex
    assert reindex.main(["--palace", str(tmp_path / "nope"), "--no-backup"]) == 2


def test_reindex_verify_without_index_creates_nothing(isolated_palace):
    import os
    import reindex
    _seed_legacy_palace()
    index_dir = os.path.join(isolated_palace, passages.INDEX_DIRNAME)
    assert reindex.main(["--palace", isolated_palace, "--verify"]) == 1
    assert not os.path.exists(index_dir)
