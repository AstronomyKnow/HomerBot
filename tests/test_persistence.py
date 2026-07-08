import os
from pathlib import Path

from backup_economy import resolve_database_paths


def test_resolve_database_paths_prefers_persistent_volume(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.delenv("ECONOMY_DB_PATH", raising=False)

    db_path, backup_path = resolve_database_paths()

    assert db_path == Path("/data/economy.db")
    assert backup_path == Path("/data/economy.db.backup")
