import os
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "economy.db"
BACKUP_PATH = ROOT / "economy.db.backup"


def backup_if_needed():
    if not DB_PATH.exists():
        return False
    if BACKUP_PATH.exists():
        try:
            if DB_PATH.stat().st_mtime <= BACKUP_PATH.stat().st_mtime:
                return True
        except OSError:
            pass
    shutil.copy2(DB_PATH, BACKUP_PATH)
    return True


if __name__ == "__main__":
    backup_if_needed()
    print("backup_economy.py finished")
