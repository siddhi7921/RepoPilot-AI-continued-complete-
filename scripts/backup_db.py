"""Create a consistent SQLite backup using SQLite's online backup API."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPOPILOT_DB_PATH", "repo_pilot.db")).resolve()
    destination_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "backups").resolve()
    if not source.is_file():
        print(f"Database does not exist: {source}", file=sys.stderr)
        return 2
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"repo_pilot-{stamp}.sqlite3"
    if destination.exists():
        print("Backup destination already exists; refusing to overwrite.", file=sys.stderr)
        return 3
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=15) as src:
        with sqlite3.connect(destination, timeout=15) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                destination.unlink(missing_ok=True)
                print("Backup integrity check failed.", file=sys.stderr)
                return 4
    os.chmod(destination, 0o600)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
