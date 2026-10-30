"""sqlite storage: users, votes, annulments and a small key-value table"""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    tg_name    TEXT,
    started_at TEXT    NOT NULL,
    banned     INTEGER NOT NULL DEFAULT 0, -- set by /annul, blocks voting
    blocked    INTEGER NOT NULL DEFAULT 0  -- user blocked the bot
);
-- one row per user: the primary key is what makes a second vote impossible
CREATE TABLE IF NOT EXISTS votes (
    user_id   INTEGER PRIMARY KEY REFERENCES users(user_id),
    candidate TEXT NOT NULL,
    voted_at  TEXT NOT NULL
);
-- the removed choice is not kept
CREATE TABLE IF NOT EXISTS annulments (
    user_id     INTEGER NOT NULL,
    admin_id    INTEGER NOT NULL,
    had_vote    INTEGER NOT NULL,
    annulled_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class VoteResult(enum.Enum):
    OK = "ok"
    ALREADY = "already"
    BANNED = "banned"
    NOT_REGISTERED = "not_registered"


@dataclass(frozen=True)
class User:
    user_id: int
    username: str | None
    tg_name: str | None
    started_at: datetime
    banned: bool
    blocked: bool


@dataclass(frozen=True)
class Stats:
    registered: int
    voted: int
    annulled: int
    blocked: int
    vote_times: list[datetime]


@dataclass(frozen=True)
class VoterRow:
    user: User
    voted: bool


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        tg_name=row["tg_name"],
        started_at=datetime.fromisoformat(row["started_at"]),
        banned=bool(row["banned"]),
        blocked=bool(row["blocked"]),
    )


class Storage:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit mode, transactions are opened explicitly where needed
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def _tx(self):
        return _Transaction(self.conn)

    # users

    def touch_user(self, user_id: int, username: str | None, tg_name: str | None, now: datetime) -> tuple[User, bool]:
        """register the user on first contact, refresh telegram names after; returns (user, created)"""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, tg_name, started_at) VALUES (?, ?, ?, ?)",
            (user_id, username, tg_name, _ts(now)),
        )
        created = cur.rowcount == 1
        if not created:
            self.conn.execute(
                "UPDATE users SET username = ?, tg_name = ?, blocked = 0 WHERE user_id = ?",
                (username, tg_name, user_id),
            )
        user = self.get_user(user_id)
        assert user is not None
        return user, created

    def get_user(self, user_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def mark_blocked(self, user_id: int) -> None:
        self.conn.execute("UPDATE users SET blocked = 1 WHERE user_id = ?", (user_id,))

    # votes

    def has_voted(self, user_id: int) -> bool:
        return self.conn.execute("SELECT 1 FROM votes WHERE user_id = ?", (user_id,)).fetchone() is not None

    def cast_vote(self, user_id: int, candidate: str, now: datetime) -> VoteResult:
        with self._tx():
            row = self.conn.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                return VoteResult.NOT_REGISTERED
            if row["banned"]:
                return VoteResult.BANNED
            try:
                self.conn.execute(
                    "INSERT INTO votes (user_id, candidate, voted_at) VALUES (?, ?, ?)",
                    (user_id, candidate, _ts(now)),
                )
            except sqlite3.IntegrityError:
                return VoteResult.ALREADY
            return VoteResult.OK

    def annul(self, user_id: int, admin_id: int, now: datetime) -> bool | None:
        """remove the vote and bar the user from voting; None if the user is unknown, else whether a vote was removed"""
        with self._tx():
            if self.get_user(user_id) is None:
                return None
            had_vote = self.conn.execute("DELETE FROM votes WHERE user_id = ?", (user_id,)).rowcount == 1
            self.conn.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "INSERT INTO annulments (user_id, admin_id, had_vote, annulled_at) VALUES (?, ?, ?, ?)",
                (user_id, admin_id, int(had_vote), _ts(now)),
            )
            return had_vote

    def tally(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT candidate, COUNT(*) AS n FROM votes GROUP BY candidate")
        return {r["candidate"]: r["n"] for r in rows}

    def stats(self) -> Stats:
        q = self.conn.execute
        return Stats(
            registered=q("SELECT COUNT(*) FROM users").fetchone()[0],
            voted=q("SELECT COUNT(*) FROM votes").fetchone()[0],
            annulled=q("SELECT COUNT(DISTINCT user_id) FROM annulments").fetchone()[0],
            blocked=q("SELECT COUNT(*) FROM users WHERE blocked = 1").fetchone()[0],
            vote_times=[datetime.fromisoformat(r[0]) for r in q("SELECT voted_at FROM votes ORDER BY voted_at")],
        )

    def voters(self) -> list[VoterRow]:
        rows = self.conn.execute(
            "SELECT u.*, v.user_id IS NOT NULL AS voted FROM users u"
            " LEFT JOIN votes v ON v.user_id = u.user_id ORDER BY u.started_at, u.user_id"
        )
        return [VoterRow(user=_user(r), voted=bool(r["voted"])) for r in rows]

    def recipients(self, not_voted_only: bool) -> list[User]:
        sql = "SELECT * FROM users WHERE blocked = 0"
        if not_voted_only:
            sql += " AND banned = 0 AND user_id NOT IN (SELECT user_id FROM votes)"
        return [_user(r) for r in self.conn.execute(sql + " ORDER BY started_at, user_id")]

    def reset_votes(self) -> int:
        """drop all votes, annulments and sent-notice marks; registrations stay"""
        with self._tx():
            n = self.conn.execute("DELETE FROM votes").rowcount
            self.conn.execute("DELETE FROM annulments")
            self.conn.execute("UPDATE users SET banned = 0")
            self.conn.execute("DELETE FROM kv WHERE key LIKE 'sent:%'")
            return n

    # key-value

    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


class _Transaction:
    """BEGIN IMMEDIATE on enter; COMMIT on a normal exit (return included), ROLLBACK on an exception"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
