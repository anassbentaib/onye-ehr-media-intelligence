import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS patients (
                    id TEXT PRIMARY KEY, bundle TEXT NOT NULL, record_hash TEXT NOT NULL,
                    summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS summary_cache (
                    cache_key TEXT PRIMARY KEY, summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def replace_patients(self, patients: list[dict], report: dict):
        """Replace a complete imported snapshot atomically; never leave deleted rows behind."""
        with self.connect() as db:
            db.execute("DELETE FROM patients")
            db.executemany("INSERT INTO patients VALUES (?, ?, ?, ?)", [
                (p["id"], json.dumps(p["bundle"]), content_hash(p["bundle"]), json.dumps(p["summary"])) for p in patients
            ])
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('report', ?)", (json.dumps(report),))

    def patients(self) -> list[dict]:
        with self.connect() as db:
            return [{"id": r["id"], "bundle": json.loads(r["bundle"]), "record_hash": r["record_hash"],
                     "summary": json.loads(r["summary"])} for r in db.execute("SELECT * FROM patients ORDER BY id")]

    def report(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()
        return json.loads(row[0]) if row else {}

    def cached_summary(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT summary FROM summary_cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_summary(self, key: str, summary: dict):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO summary_cache VALUES (?, ?)", (key, json.dumps(summary)))

