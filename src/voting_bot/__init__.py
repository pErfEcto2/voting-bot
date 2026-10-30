"""telegram bot for a two-candidate (or more) election with a fixed voting window"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def _check() -> int:
    from .config import load_settings, now_utc
    from .texts import fmt_window

    s = load_settings()
    e = s.election
    print(f"title:      {e.title}")
    print(f"window:     {fmt_window(e)}")
    print(f"start/end:  {e.start.isoformat()} .. {e.end.isoformat()}")
    print(f"reminder:   {e.reminder.isoformat() if e.reminder else '-'}")
    print(f"phase now:  {e.phase(now_utc()).value}")
    print(f"live admin results: {e.admin_live_results}")
    print(f"source url: {e.source_url}")
    for c in e.candidates:
        state = "missing!" if c.program and not c.program.is_file() else "ok"
        print(f"candidate:  {c.id}: {c.name} ({c.about}) program={c.program} {state if c.program else ''}")
    print(f"admins:     {len(s.admin_ids)}")
    print(f"db:         {s.db_path}")
    return 0


def _reset_votes(yes: bool) -> int:
    from .config import db_path
    from .db import Storage

    path = db_path()
    if not Path(path).exists():
        print(f"no database at {path}", file=sys.stderr)
        return 1
    if not yes:
        print("this deletes every vote and annulment; run again with --yes", file=sys.stderr)
        return 1
    db = Storage(path)
    n = db.reset_votes()
    db.close()
    print(f"deleted {n} votes from {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="voting-bot")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the bot (default)")
    sub.add_parser("check", help="validate the configuration and print a summary")
    reset = sub.add_parser("reset-votes", help="delete all votes, keep registrations (stop the bot first)")
    reset.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.cmd == "check":
        sys.exit(_check())
    if args.cmd == "reset-votes":
        sys.exit(_reset_votes(args.yes))

    from .app import run
    from .config import load_settings

    asyncio.run(run(load_settings()))
