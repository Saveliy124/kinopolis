"""Online SQLite backup, including committed data from the WAL file."""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def backup_database(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination or not source.is_file():
        raise ValueError("Source database must exist and differ from destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace an existing backup, even when two commands run at once.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup")
    parser.add_argument("--database", type=Path, default=root / "data" / "cinema.db")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = args.output or root / "backups" / f"cinema-{stamp}-{uuid4().hex[:6]}.db"
    backup_database(args.database, output)
    print(f"Backup verified: {output.resolve()}")


if __name__ == "__main__":
    main()
