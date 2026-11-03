"""Build / repair / verify the passage index of a palace (see passages.py).

This is the migration for the passage index. It never writes to the article
collection; it only reads articles and writes the derived index. To *prove*
that, it records a manifest (sha256 of every article's id, content and
metadata) before building and checks it again afterwards.

    uv run python reindex.py --palace PATH            # backup + build + verify
    uv run python reindex.py --palace PATH --verify   # verify only
    uv run python reindex.py --palace PATH --no-backup

Stop every writer of the palace before running against a live palace when a
backup is taken (a copy of a SQLite file under write may be torn). Articles
*added* during the build are allowed and indexed by reconcile's catch-up
passes: verification checks that every article present before is still present
and byte-identical, not that nothing was added. Articles *deleted or edited*
by a writer during the run are reported as missing/modified — rerun with
writers stopped to get a clean verification.

Exit status: 0 = verified, 1 = verification failed, 2 = usage / setup error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime

import passages


EXPECTED_CHROMADB = "1.5.7"  # keep in sync with uv.lock


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def open_articles(palace: str):
    import chromadb
    from mempalace.config import MempalaceConfig

    client = chromadb.PersistentClient(path=palace)
    # get_collection, never get_or_create: this tool must not create or alter
    # anything in the article store.
    return client.get_collection(MempalaceConfig().collection_name)


def article_digest(did: str, doc: str | None, meta: dict | None) -> str:
    h = hashlib.sha256()
    h.update(did.encode())
    h.update(b"\0")
    h.update((doc if doc is not None else "\0<none>").encode())
    h.update(b"\0")
    h.update(json.dumps(meta or {}, sort_keys=True, ensure_ascii=False).encode())
    return h.hexdigest()


def iter_articles(col, page: int = 500):
    offset = 0
    while True:
        b = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
        if not b["ids"]:
            return
        for i, did in enumerate(b["ids"]):
            yield did, b["documents"][i], b["metadatas"][i]
        offset += len(b["ids"])


def manifest(col) -> dict[str, str]:
    m = {}
    for did, doc, meta in iter_articles(col):
        m[did] = article_digest(did, doc, meta)
    return m


def backup(palace: str, dest_root: str, index_path: str) -> str:
    dest = os.path.join(dest_root, f"palace-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    index_abs = os.path.abspath(index_path)

    def ignore(dirpath, names):
        return [n for n in names if os.path.abspath(os.path.join(dirpath, n)) == index_abs]

    shutil.copytree(palace, dest, ignore=ignore)
    return dest


def verify_index(col, index: passages.PassageIndex) -> list[str]:
    """Check the index against the article store. Returns a list of problems."""
    problems: list[str] = []
    state = {
        row[0]: row[1:]
        for row in index._db.execute(
            "SELECT drawer_id, content_hash, chunk_count, chunker_version FROM articles"
        )
    }
    # Gather all passages once (paged).
    by_drawer: dict[str, list[tuple[int, int, int, str]]] = {}
    pcol = index._col
    offset = 0
    while True:
        b = pcol.get(include=["metadatas"], limit=5000, offset=offset)
        if not b["ids"]:
            break
        for pid, m in zip(b["ids"], b["metadatas"]):
            did = m.get("drawer_id")
            if not did or pid != f"{did}::p{int(m.get('chunk_index', -1)):05d}":
                problems.append(f"malformed passage {pid}")
                continue
            by_drawer.setdefault(did, []).append(
                (int(m["chunk_index"]), int(m["start"]), int(m["end"]), m.get("content_hash", ""))
            )
        offset += len(b["ids"])

    seen = set()
    for did, doc, _meta in iter_articles(col):
        seen.add(did)
        doc = doc or ""
        st = state.get(did)
        if st is None:
            problems.append(f"{did}: not indexed")
            continue
        h, count, version = st
        if version != passages.CHUNKER_VERSION:
            problems.append(f"{did}: chunker version {version} != {passages.CHUNKER_VERSION}")
        if h != passages.content_hash(doc):
            problems.append(f"{did}: index built from different content")
            continue
        ps = sorted(by_drawer.get(did, []))
        if len(ps) != count or [p[0] for p in ps] != list(range(count)):
            problems.append(f"{did}: expected {count} passages, found {len(ps)}")
            continue
        if any(p[3] != h for p in ps):
            problems.append(f"{did}: passage hash mismatch")
            continue
        if doc.strip() and count == 0:
            problems.append(f"{did}: non-empty article without passages")
            continue
        # Coverage: passages tile [0, len(doc)) with overlaps, no gaps.
        if count:
            if ps[0][1] != 0 or ps[-1][2] != len(doc):
                problems.append(f"{did}: passages do not span the whole article")
                continue
            for prev, cur in zip(ps, ps[1:]):
                if not (prev[1] < cur[1] <= prev[2]):
                    problems.append(f"{did}: gap/disorder between passages {prev[0]} and {cur[0]}")
                    break
    for did in by_drawer.keys() - seen:
        problems.append(f"{did}: orphan passages ({len(by_drawer[did])})")
    for did in state.keys() - seen:
        problems.append(f"{did}: orphan state row")
    # Smoke-check the vector segment itself (metadata checks never touch HNSW).
    if pcol.count():
        try:
            pcol.query(query_embeddings=[[0.0] * 384], n_results=1)
        except Exception as e:
            problems.append(f"passage vector index unreadable: {e}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--palace", default=os.environ.get("MEMPALACE_PALACE_PATH"), help="palace directory")
    ap.add_argument("--index", help="index directory (default: <palace>/memorymark-passages or $MEMORYMARK_INDEX_PATH)")
    ap.add_argument("--backup-dir", help="where to put the palace backup (default: <palace>/../backups)")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--verify", action="store_true", help="verify only, do not build")
    ap.add_argument("--quick", action="store_true", help="skip content hashing when deciding what to rebuild")
    args = ap.parse_args(argv)

    if not args.palace:
        print("--palace (or MEMPALACE_PALACE_PATH) is required", file=sys.stderr)
        return 2
    palace = os.path.abspath(os.path.expanduser(args.palace))

    import chromadb

    if chromadb.__version__ != EXPECTED_CHROMADB:
        # A different chroma could migrate the palace schema in place on open.
        print(f"chromadb {chromadb.__version__} != expected {EXPECTED_CHROMADB}; refusing", file=sys.stderr)
        return 2

    index_path = os.path.abspath(args.index or passages.default_index_path(palace))
    if not os.path.isfile(os.path.join(palace, "chroma.sqlite3")):
        print(f"no palace at {palace}", file=sys.stderr)
        return 2
    if args.verify and not os.path.isfile(os.path.join(index_path, "chroma.sqlite3")):
        # Verify must not create anything (PassageIndex would create the store).
        print(f"no passage index at {index_path}", file=sys.stderr)
        return 1
    col = open_articles(palace)

    if not args.verify and not args.no_backup:
        root = args.backup_dir or os.path.join(os.path.dirname(palace), "backups")
        os.makedirs(root, exist_ok=True)
        log(f"backing up {palace} -> {root} (index dir excluded)")
        dest = backup(palace, root, index_path)
        log(f"backup at {dest}")

    log("computing article manifest (before)")
    before = manifest(col)
    log(f"{len(before)} articles")

    index = passages.PassageIndex(index_path, lambda: col)
    if not args.verify:
        started = time.time()
        last = [0.0]

        def progress(rs: passages.ReconcileStatus) -> None:
            now = time.time()
            if rs.state == "running" and now - last[0] < 30 and rs.processed not in (0, rs.total):
                return
            last[0] = now
            el = now - started
            rate = rs.processed / el if el > 0 and rs.processed else 0
            eta = (rs.total - rs.processed) / rate if rate else 0
            log(
                f"{rs.state}: {rs.processed}/{rs.total} articles, indexed {rs.indexed}, "
                f"removed {rs.removed}, failed {rs.failed}, passages {index.passage_count()}, "
                f"eta {eta / 60:.0f} min"
            )

        rs = index.reconcile(full=not args.quick, progress=progress)
        if rs.state != "done" or rs.failed:
            log(f"reconcile did not complete cleanly: {rs.as_dict()}")

    log("verifying article store (after)")
    after = manifest(col)
    lost = [d for d in before if d not in after]
    changed = [d for d in before if d in after and after[d] != before[d]]
    added = len(after.keys() - before.keys())
    ok = True
    if lost or changed:
        ok = False
        log(f"ARTICLE STORE CHANGED: {len(lost)} missing, {len(changed)} modified "
            f"(e.g. {(lost + changed)[:5]}) — writers active during the run?")
    else:
        log(f"article store intact: all {len(before)} articles present and identical ({added} added during run)")

    log("verifying passage index")
    problems = verify_index(col, index)
    if problems:
        ok = False
        log(f"INDEX PROBLEMS: {len(problems)}")
        for p in problems[:30]:
            log(f"  {p}")
    else:
        log(f"index verified: {index.indexed_count()} articles, {index.passage_count()} passages")
    index.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
