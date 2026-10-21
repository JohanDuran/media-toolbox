"""Job history in SQLite, so the queue and past results survive restarts."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime

from .config import DB_PATH

FINISHED = ("done", "failed", "cancelled", "interrupted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  tool TEXT NOT NULL,
  worker TEXT NOT NULL,
  gpu INTEGER NOT NULL,
  source TEXT,
  status TEXT NOT NULL,
  params TEXT,          -- as submitted (Windows paths)
  cparams TEXT,         -- as sent to the worker (container paths)
  result TEXT,
  error TEXT,
  logs TEXT NOT NULL DEFAULT '',
  progress REAL,
  created REAL NOT NULL,
  started REAL,
  finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
"""


class JobDB:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(_SCHEMA)
            # Jobs that were running when the gateway stopped can't be followed any more.
            self.conn.execute("UPDATE jobs SET status='interrupted', finished=? WHERE status='running'",
                              (time.time(),))
            self.conn.commit()

    def _row(self, r: sqlite3.Row | None, logs: bool = True) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in ("params", "cparams", "result"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        if not logs:
            d.pop("logs", None)
            d.pop("cparams", None)
        return d

    def create(self, tool: str, worker: str, gpu: bool, params: dict, cparams: dict, source: str) -> dict:
        job_id = f"{datetime.now():%m%d-%H%M%S}-{secrets.token_hex(2)}"
        with self.lock:
            self.conn.execute(
                "INSERT INTO jobs (id, tool, worker, gpu, source, status, params, cparams, created)"
                " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (job_id, tool, worker, int(gpu), source, json.dumps(params), json.dumps(cparams), time.time()))
            self.conn.commit()
        return self.get(job_id)

    def get(self, job_id: str, logs: bool = True) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(r, logs)

    def list(self, limit: int = 50, status: str | None = None, tool: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if tool:
            q += " AND tool=?"
            args.append(tool)
        q += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
        return [self._row(r, logs=False) for r in rows]

    def queued(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created").fetchall()
        return [self._row(r) for r in rows]

    def counts(self) -> dict:
        with self.lock:
            rows = self.conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        for k in ("result",):
            if k in fields and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            self.conn.commit()

    def append_logs(self, job_id: str, lines: list[str]) -> None:
        if not lines:
            return
        with self.lock:
            self.conn.execute("UPDATE jobs SET logs = logs || ? WHERE id=?", ("\n".join(lines) + "\n", job_id))
            self.conn.commit()
