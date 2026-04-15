import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_palace(tmp_path, monkeypatch):
    """Point MemPalace at a temporary directory so tests don't touch real data."""
    palace_path = str(tmp_path / "palace")
    os.makedirs(palace_path, exist_ok=True)
    monkeypatch.setenv("MEMPALACE_PALACE_PATH", palace_path)

    # Redirect WAL to temp directory (module-level constant, must monkeypatch)
    wal_dir = tmp_path / "wal"
    wal_dir.mkdir()
    import mempalace.mcp_server as mcp
    monkeypatch.setattr(mcp, "_WAL_DIR", wal_dir)
    monkeypatch.setattr(mcp, "_WAL_FILE", wal_dir / "write_log.jsonl")

    # Reset all cached state in mempalace.mcp_server. Clearing ChromaDB's
    # shared system cache is required so file descriptors held by the prior
    # PersistentClient are released — otherwise fds accumulate across tests
    # and chromadb's Rust backend panics with EMFILE (OS error 24).
    import chromadb
    chromadb.api.client.SharedSystemClient.clear_system_cache()

    mcp._config = mcp.MempalaceConfig()
    mcp._client_cache = None
    mcp._collection_cache = None
    mcp._metadata_cache = None
    mcp._metadata_cache_time = 0
    mcp._palace_db_inode = 0
    mcp._palace_db_mtime = 0.0

    yield palace_path

    chromadb.api.client.SharedSystemClient.clear_system_cache()
    mcp._client_cache = None
    mcp._collection_cache = None


@pytest.fixture
def client():
    from server import app
    with TestClient(app) as c:
        yield c
