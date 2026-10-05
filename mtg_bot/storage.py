import sqlite3
import time
from pathlib import Path


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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, token TEXT, text TEXT, received REAL, state TEXT)"
        )
        self.db.execute(
            "UPDATE events SET state='uncertain', token='', text='' WHERE state='processing'"
        )
        self.db.commit()

    def enqueue(self, events: list[dict]):
        now = self.clock()
        with self.db:
            self.db.execute(
                "DELETE FROM events WHERE received < ? AND state NOT IN ('queued','processing')",
                (now - 86400,),
            )
            self.db.execute(
                "UPDATE events SET state='expired', token='', text='' WHERE state='queued' AND received < ?",
                (now - 40,),
            )
            for event in events:
                if self.db.execute(
                    "SELECT 1 FROM events WHERE id=?", (event["id"],)
                ).fetchone():
                    continue
                count = self.db.execute(
                    "SELECT COUNT(*) FROM events WHERE state IN ('queued','processing')"
                ).fetchone()[0]
                if count >= self.capacity:
                    raise InboxFull()
                self.db.execute(
                    "INSERT INTO events VALUES (?,?,?,?,'queued')",
                    (event["id"], event["token"], event["text"], now),
                )

    def claim(self):
        with self.db:
            row = self.db.execute(
                "SELECT id,token,text,received FROM events WHERE state='queued' ORDER BY received,id LIMIT 1"
            ).fetchone()
            if row:
                self.db.execute(
                    "UPDATE events SET state='processing' WHERE id=?", (row[0],)
                )
        return row

    def finish(self, event_id: str, state: str):
        with self.db:
            self.db.execute(
                "UPDATE events SET state=?, token='', text='' WHERE id=?",
                (state, event_id),
            )

    def close(self):
        self.db.close()
