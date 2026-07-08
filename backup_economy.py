import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def resolve_database_paths(db_name: str = "economy.db"):
    env_path = os.getenv("ECONOMY_DB_PATH") or os.getenv("DATABASE_PATH")
    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")

    if env_path:
        db_path = Path(env_path).expanduser().resolve()
    elif volume_path:
        db_path = Path(volume_path).expanduser().resolve() / db_name
    else:
        db_path = ROOT / db_name

    backup_path = db_path.with_suffix(".db.backup")
    return db_path, backup_path


def backup_if_needed(db_name: str = "economy.db"):
    db_path, backup_path = resolve_database_paths(db_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        return False
    if backup_path.exists():
        try:
            if db_path.stat().st_mtime <= backup_path.stat().st_mtime:
                return True
        except OSError:
            pass
    shutil.copy2(db_path, backup_path)
    return True


if __name__ == "__main__":
    backup_if_needed()
    print("backup_economy.py finished")
