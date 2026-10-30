"""settings from environment variables and the election file (TOML)"""

from __future__ import annotations

import enum
import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_SOURCE_URL = "https://github.com/pErfEcto2/voting-bot"


def now_utc() -> datetime:
    return datetime.now(UTC)


class ConfigError(Exception):
    pass


class Phase(enum.Enum):
    BEFORE = "before"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    about: str
    program: Path | None


@dataclass(frozen=True)
class Election:
    title: str
    tz: ZoneInfo
    start: datetime
    end: datetime
    reminder: datetime | None
    candidates: tuple[Candidate, ...]
    admin_live_results: bool
    source_url: str

    def phase(self, now: datetime) -> Phase:
        """voting is open for start <= now < end"""
        if now < self.start:
            return Phase.BEFORE
        if now < self.end:
            return Phase.OPEN
        return Phase.CLOSED

    def candidate(self, candidate_id: str) -> Candidate | None:
        for c in self.candidates:
            if c.id == candidate_id:
                return c
        return None


@dataclass(frozen=True)
class Settings:
    token: str
    admin_ids: frozenset[int]
    db_path: Path
    election: Election

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


def _aware(value: object, key: str) -> datetime:
    if not isinstance(value, datetime):
        raise ConfigError(f"{key}: expected a TOML datetime, got {value!r}")
    if value.tzinfo is None:
        raise ConfigError(f"{key}: datetime needs a UTC offset, e.g. 2026-11-02T10:00:00+03:00")
    return value


def load_election(path: Path) -> Election:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    try:
        tz = ZoneInfo(raw.get("timezone", "Europe/Moscow"))
        start = _aware(raw["start"], "start")
        end = _aware(raw["end"], "end")
    except KeyError as e:
        raise ConfigError(f"{path}: missing key {e}") from None
    if start >= end:
        raise ConfigError("start must be before end")

    reminder = None
    if "reminder" in raw:
        reminder = _aware(raw["reminder"], "reminder")
        if not start <= reminder < end:
            raise ConfigError("reminder must be inside the voting window")

    candidates = []
    for i, c in enumerate(raw.get("candidates", [])):
        try:
            cid, name = str(c["id"]), str(c["name"])
        except KeyError as e:
            raise ConfigError(f"candidates[{i}]: missing key {e}") from None
        program = c.get("program")
        candidates.append(
            Candidate(
                id=cid,
                name=name,
                about=str(c.get("about", "")),
                program=(path.parent / program) if program else None,
            )
        )
    if len(candidates) < 2:
        raise ConfigError("need at least two candidates")
    ids = [c.id for c in candidates]
    if len(set(ids)) != len(ids):
        raise ConfigError("candidate ids must be unique")
    # candidate ids go into callback data, which Telegram limits to 64 bytes
    if any(len(cid.encode()) > 32 or ":" in cid for cid in ids):
        raise ConfigError("candidate id: at most 32 bytes, no ':'")

    return Election(
        title=str(raw.get("title", "Голосование")),
        tz=tz,
        start=start,
        end=end,
        reminder=reminder,
        candidates=tuple(candidates),
        admin_live_results=bool(raw.get("admin_live_results", True)),
        source_url=str(raw.get("source_url", DEFAULT_SOURCE_URL)),
    )


def parse_admin_ids(value: str) -> frozenset[int]:
    ids = set()
    for part in value.replace(" ", "").split(","):
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(f"ADMIN_IDS: not a number: {part!r}")
        ids.add(int(part))
    return frozenset(ids)


def read_token() -> str:
    token_file = os.environ.get("BOT_TOKEN_FILE")
    if token_file:
        token = Path(token_file).read_text().strip()
    else:
        token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("set BOT_TOKEN_FILE (preferred) or BOT_TOKEN")
    return token


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "data/votebot.db"))


def load_settings() -> Settings:
    election = load_election(Path(os.environ.get("ELECTION_FILE", "election.toml")))
    admin_ids = parse_admin_ids(os.environ.get("ADMIN_IDS", ""))
    if not admin_ids:
        raise ConfigError("ADMIN_IDS is empty")
    return Settings(
        token=read_token(),
        admin_ids=admin_ids,
        db_path=db_path(),
        election=election,
    )
