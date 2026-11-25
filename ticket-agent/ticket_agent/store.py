"""SQLite-Speicher: welche Inserate schon gesehen/kontaktiert wurden."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS listings (key TEXT PRIMARY KEY, title TEXT, url TEXT, price REAL,"
            " status TEXT, first_seen REAL, updated REAL)"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS messages (key TEXT, sent REAL)")
        self.db.commit()

    def get(self, key: str) -> tuple[float | None, str] | None:
        row = self.db.execute("SELECT price, status FROM listings WHERE key=?", (key,)).fetchone()
        return (row[0], row[1]) if row else None

    def save(self, listing, status: str) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO listings VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET"
            " price=excluded.price, status=excluded.status, updated=excluded.updated",
            (listing.key, listing.title, listing.url, listing.price, status, now, now),
        )
        self.db.commit()

    def record_message(self, key: str) -> None:
        self.db.execute("INSERT INTO messages VALUES (?,?)", (key, time.time()))
        self.db.commit()

    def messages_last_24h(self) -> int:
        since = time.time() - 86400
        return self.db.execute("SELECT COUNT(*) FROM messages WHERE sent>?", (since,)).fetchone()[0]

    def recent(self, limit: int = 30) -> list[tuple]:
        return self.db.execute(
            "SELECT status, price, title, url FROM listings WHERE status!='ignored' ORDER BY updated DESC LIMIT ?",
            (limit,),
        ).fetchall()
