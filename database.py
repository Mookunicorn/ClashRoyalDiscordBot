"""Persistance SQLite : joueurs, passages dans le clan, clans déjà croisés."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    tag         TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    discord_id  INTEGER,
    role        TEXT,
    verified_at TEXT
);
CREATE TABLE IF NOT EXISTS stints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_tag  TEXT NOT NULL,
    joined_at   TEXT NOT NULL,
    left_at     TEXT,
    initial     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stints_tag ON stints(player_tag);
CREATE TABLE IF NOT EXISTS known_clans (
    player_tag  TEXT NOT NULL,
    clan_tag    TEXT NOT NULL,
    clan_name   TEXT,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (player_tag, clan_tag)
);
CREATE TABLE IF NOT EXISTS rank_roles (
    rank        TEXT PRIMARY KEY,
    role_id     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders (day TEXT, slot TEXT, PRIMARY KEY (day, slot));
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Database:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        try:  # migration des bases créées avant l'ajout de la vérification par jeton
            await self._db.execute("ALTER TABLE players ADD COLUMN verified_at TEXT")
        except sqlite3.OperationalError:
            pass
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------ helpers
    async def _run(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        await self._db.commit()
        return cur

    async def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        assert self._db is not None
        async with self._db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ meta
    async def get_meta(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self._run(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ joueurs
    async def upsert_player(self, tag: str, name: str, role: str | None = None) -> None:
        await self._run(
            "INSERT INTO players(tag, name, role) VALUES(?, ?, ?) "
            "ON CONFLICT(tag) DO UPDATE SET name=excluded.name, "
            "role=COALESCE(excluded.role, players.role)",
            (tag, name, role),
        )

    async def get_player(self, tag: str) -> dict | None:
        return await self._one("SELECT * FROM players WHERE tag=?", (tag,))

    async def set_discord_id(
        self, tag: str, discord_id: int | None, name: str | None = None, verified: bool = False
    ) -> None:
        """Lie (ou délie si None) un compte Discord ; `verified` = prouvé par jeton API."""
        verified_at = now_iso() if (verified and discord_id is not None) else None
        await self._run(
            "INSERT INTO players(tag, name, discord_id, verified_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(tag) DO UPDATE SET discord_id=excluded.discord_id, verified_at=excluded.verified_at",
            (tag, name or tag, discord_id, verified_at),
        )

    async def find_by_discord(self, discord_id: int) -> dict | None:
        return await self._one("SELECT * FROM players WHERE discord_id=?", (discord_id,))

    async def linked_players(self) -> list[dict]:
        return await self._all("SELECT * FROM players WHERE discord_id IS NOT NULL")


    # ------------------------------------------------------------------ passages dans le clan
    async def open_tags(self) -> set[str]:
        rows = await self._all("SELECT player_tag FROM stints WHERE left_at IS NULL")
        return {r["player_tag"] for r in rows}

    async def open_stint(self, tag: str, initial: bool = False) -> None:
        await self._run(
            "INSERT INTO stints(player_tag, joined_at, initial) VALUES(?, ?, ?)",
            (tag, now_iso(), int(initial)),
        )

    async def close_stint(self, tag: str) -> dict | None:
        row = await self._one(
            "SELECT * FROM stints WHERE player_tag=? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
            (tag,),
        )
        if row:
            await self._run("UPDATE stints SET left_at=? WHERE id=?", (now_iso(), row["id"]))
            row["left_at"] = now_iso()
        return row

    async def stints(self, tag: str) -> list[dict]:
        return await self._all("SELECT * FROM stints WHERE player_tag=? ORDER BY id", (tag,))

    # ------------------------------------------------------------------ clans déjà croisés
    async def upsert_known_clan(self, player_tag: str, clan_tag: str, clan_name: str, last_seen: str) -> None:
        await self._run(
            "INSERT INTO known_clans(player_tag, clan_tag, clan_name, last_seen) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(player_tag, clan_tag) DO UPDATE SET clan_name=excluded.clan_name, "
            "last_seen=MAX(known_clans.last_seen, excluded.last_seen)",
            (player_tag, clan_tag, clan_name, last_seen),
        )

    async def known_clans(self, player_tag: str, exclude: str | None = None, limit: int = 5) -> list[dict]:
        return await self._all(
            "SELECT * FROM known_clans WHERE player_tag=? AND clan_tag != ? "
            "ORDER BY last_seen DESC LIMIT ?",
            (player_tag, exclude or "", limit),
        )

    # ------------------------------------------------------------------ réglages
    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._run(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in await self._all("SELECT key, value FROM settings")}

    # ------------------------------------------------------------------ changement de clan
    async def has_tracking(self) -> bool:
        return await self._one("SELECT 1 AS x FROM stints LIMIT 1") is not None

    async def reset_tracking(self) -> None:
        """Repart de zéro pour un nouveau clan (les liaisons Discord sont conservées)."""
        await self._run("DELETE FROM stints")
        await self._run("DELETE FROM reminders")
        await self._run("DELETE FROM meta WHERE key IN ('bootstrapped', 'last_war', 'clan_settings')")

    # ------------------------------------------------------------------ rangs -> rôles Discord
    async def rank_roles(self) -> dict[str, int]:
        rows = await self._all("SELECT rank, role_id FROM rank_roles")
        return {r["rank"]: r["role_id"] for r in rows}

    async def set_rank_role(self, rank: str, role_id: int) -> None:
        await self._run(
            "INSERT INTO rank_roles(rank, role_id) VALUES(?, ?) "
            "ON CONFLICT(rank) DO UPDATE SET role_id=excluded.role_id",
            (rank, role_id),
        )

    async def del_rank_role(self, rank: str) -> None:
        await self._run("DELETE FROM rank_roles WHERE rank=?", (rank,))

    # ------------------------------------------------------------------ rappels
    async def reminder_sent(self, day: str, slot: str) -> bool:
        return await self._one("SELECT 1 AS x FROM reminders WHERE day=? AND slot=?", (day, slot)) is not None

    async def mark_reminder(self, day: str, slot: str) -> None:
        await self._run("INSERT OR IGNORE INTO reminders(day, slot) VALUES(?, ?)", (day, slot))
