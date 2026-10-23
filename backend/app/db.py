"""SQLite access layer (schema is normative — SPEC §2)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id            TEXT PRIMARY KEY,
  ip            TEXT NOT NULL UNIQUE,
  gen           INTEGER NOT NULL,
  model         TEXT NOT NULL,
  name          TEXT,
  fw_version    TEXT,
  profile       TEXT,
  auth_required INTEGER NOT NULL DEFAULT 0,
  online        INTEGER NOT NULL DEFAULT 1,
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  raw_info      TEXT
);

CREATE TABLE IF NOT EXISTS channels (
  id         TEXT PRIMARY KEY,
  device_id  TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  idx        INTEGER NOT NULL,
  name       TEXT,
  UNIQUE(device_id, kind, idx)
);

CREATE TABLE IF NOT EXISTS action_slots (
  id           TEXT PRIMARY KEY,
  device_id    TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  source_kind  TEXT NOT NULL,
  source_idx   INTEGER NOT NULL,
  event        TEXT NOT NULL,
  native_key   TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 0,
  name         TEXT
);

CREATE TABLE IF NOT EXISTS action_urls (
  id         TEXT PRIMARY KEY,
  slot_id    TEXT NOT NULL REFERENCES action_slots(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL,
  raw_url    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
  id                TEXT PRIMARY KEY,
  action_url_id     TEXT NOT NULL REFERENCES action_urls(id) ON DELETE CASCADE,
  src_slot_id       TEXT NOT NULL,
  target_type       TEXT NOT NULL,
  target_device_id  TEXT,
  target_channel_id TEXT,
  external_host     TEXT,
  external_path     TEXT,
  command           TEXT NOT NULL,
  params            TEXT,
  status            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
  device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
  username  TEXT NOT NULL,
  password  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_snapshots (
  id         TEXT PRIMARY KEY,
  device_id  TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  taken_at   TEXT NOT NULL,
  config     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id         TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at   TEXT,
  method     TEXT NOT NULL,
  found      INTEGER DEFAULT 0,
  errors     TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id        TEXT PRIMARY KEY,
  ts        TEXT NOT NULL,
  device_id TEXT NOT NULL,
  action    TEXT NOT NULL,
  payload   TEXT NOT NULL,
  result    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_layout (
  node_id TEXT PRIMARY KEY,
  x REAL NOT NULL, y REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channels_device ON channels(device_id);
CREATE INDEX IF NOT EXISTS idx_slots_device ON action_slots(device_id);
CREATE INDEX IF NOT EXISTS idx_urls_slot ON action_urls(slot_id);
CREATE INDEX IF NOT EXISTS idx_edges_slot ON edges(src_slot_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_device ON config_snapshots(device_id, taken_at);
"""


class Database:
    """One connection, serialized through a lock (single-writer workload)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        async with self._lock:
            await self.conn.execute(sql, params)
            await self.conn.commit()

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        async with self._lock:
            await self.conn.executemany(sql, rows)
            await self.conn.commit()

    async def script(self, sql: str) -> None:
        async with self._lock:
            await self.conn.executescript(sql)
            await self.conn.commit()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            try:
                return list(await cur.fetchall())
            finally:
                await cur.close()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None


db: Database | None = None


def get_db() -> Database:
    if db is None:
        raise RuntimeError("database is not initialised")
    return db


async def init_db(path: Path) -> Database:
    global db
    db = Database(path)
    await db.connect()
    return db
