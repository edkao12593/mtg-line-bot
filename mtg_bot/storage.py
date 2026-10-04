import asyncio
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from .models import ImageResponse


class LocalImageStore:
    """Public immutable content-addressed images; bounded disk retention."""
    def __init__(self, directory: Path, base_url: str, *, ttl: float = 7 * 86400, quota: int = 256_000_000):
        self.directory, self.base_url = directory, base_url.rstrip("/")
        self.ttl, self.quota = ttl, quota
        self.lock = asyncio.Lock()
        directory.mkdir(parents=True, exist_ok=True)

    async def publish(self, original: bytes, preview: bytes) -> ImageResponse:
        def write():
            now = time.time()
            files = list(self.directory.glob("*.jpg"))
            for path in files:
                if now - path.stat().st_mtime > self.ttl:
                    path.unlink(missing_ok=True)
            files = sorted(self.directory.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            reserve = len(original) + len(preview)
            for path in files:
                if total + reserve <= self.quota:
                    break
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
            if reserve > self.quota:
                raise ValueError("image store quota exceeded")
            urls = []
            for data in (original, preview):
                name = hashlib.sha256(data).hexdigest() + ".jpg"
                target = self.directory / name
                if not target.exists():
                    temp = self.directory / (name + ".tmp")
                    temp.write_bytes(data)
                    temp.replace(target)
                os.utime(target, None)
                urls.append(f"{self.base_url}/images/{name}")
            return ImageResponse(*urls)
        async with self.lock:
            return await asyncio.to_thread(write)


class InboxFull(Exception):
    pass


class Inbox:
    """Single-process durable work queue. Store only needed text/token fields.

    Processing rows after restart are terminal 'uncertain': never blindly replay
    a possibly-used reply token. Queued rows remain recoverable if not expired.
    """
    def __init__(self, path: Path, *, capacity: int = 20, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.clock, self.capacity = clock, capacity
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, token TEXT, text TEXT, received REAL, state TEXT)")
        self.db.execute("UPDATE events SET state='uncertain', token='', text='' WHERE state='processing'")
        self.db.commit()

    def enqueue(self, events: list[dict]):
        now = self.clock()
        with self.db:
            self.db.execute("DELETE FROM events WHERE received < ? AND state NOT IN ('queued','processing')", (now - 86400,))
            self.db.execute("UPDATE events SET state='expired', token='', text='' WHERE state='queued' AND received < ?", (now - 40,))
            for event in events:
                if self.db.execute("SELECT 1 FROM events WHERE id=?", (event["id"],)).fetchone():
                    continue
                count = self.db.execute("SELECT COUNT(*) FROM events WHERE state IN ('queued','processing')").fetchone()[0]
                if count >= self.capacity:
                    raise InboxFull()
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,'queued')", (event["id"], event["token"], event["text"], now))

    def claim(self):
        with self.db:
            row = self.db.execute("SELECT id,token,text,received FROM events WHERE state='queued' ORDER BY received,id LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE events SET state='processing' WHERE id=?", (row[0],))
        return row

    def finish(self, event_id: str, state: str):
        with self.db:
            self.db.execute("UPDATE events SET state=?, token='', text='' WHERE id=?", (state, event_id))

    def close(self):
        self.db.close()
