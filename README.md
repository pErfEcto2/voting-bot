# voting-bot

A Telegram bot for a small election with a fixed voting window. It was written for the election of the chair of НСО ВШУ on 2 November 2026, but the candidates, times and texts around them come from a config file, so it works for any election with two or more candidates.

All messages the bot sends are in Russian.

## How it works

**Registration.** Any message to the bot registers the Telegram account. The bot answers with a greeting, the list of candidates, the voting time, a link to this repository and the candidates' programs as PDF files. Registration stays open until voting ends.

**Voting.** When voting opens, the bot sends every registered user a message with one button per candidate. A vote needs a second tap to confirm. Each Telegram account has one vote, and a confirmed vote cannot be changed. The bot checks the time when the vote arrives, so a button pressed after the end is refused even if the message was sent earlier. Users who first write to the bot while voting is open get the buttons right after the greeting.

**Notices.** One hour before the end, users who have not voted get a reminder. At the end, the admins get the final results. Each notice is sent once, and a restart does not repeat it.

**Secrecy.** Admin commands show totals only. The database stores which account voted for whom, because `/annul` has to remove one specific vote. Logs and the `/voters` export never contain the choice. Anyone with root access to the server can read the database.

## Admin commands

Admins are listed by numeric Telegram id in `ADMIN_IDS`. Anyone else who sends these commands gets "Вы не администратор."

| Command | What it does |
|---|---|
| `/results` | Votes per candidate with percentages. Before the end it shows numbers only if `admin_live_results = true`. |
| `/stats` | Registered users, votes, turnout, votes per hour. |
| `/voters` | Excel file (.xlsx): user id, username, Telegram name, registration time, voted yes/no, annulled, blocked the bot. Every text cell is stored as text, so a name that starts with "=" or "@" never runs as a formula. |
| `/annul <id>` | Removes the vote of that user and bars them from voting again. |
| `/broadcast <text>` | Sends a text to every registered user, after a confirm button. |
| `/admin` | Lists these commands. |

## Configuration

Two files, both git-ignored:

- `election.toml`: title, time zone, start, end, reminder, candidates and paths to their PDF programs. See `election.example.toml`. Times need an explicit UTC offset, for example `2026-11-02T10:00:00+03:00`. Voting is open for `start <= now < end`.
- `.env`: `BOT_TOKEN_FILE` (path to a file with the token from @BotFather), `ADMIN_IDS`, `ELECTION_FILE`, `DB_PATH`, `LOG_LEVEL`. See `.env.example`.

Data is kept in SQLite.

## Running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp election.example.toml election.toml   # edit it, put the PDFs into programs/
cp .env.example .env                     # edit it
set -a; . ./.env; set +a
uv run voting-bot check   # validates the config and prints a summary
uv run voting-bot run
```

The bot uses long polling, so it needs no open port and no domain.

## Deploying with systemd

```sh
useradd --system --home-dir /opt/voting-bot --no-create-home --shell /usr/sbin/nologin votebot
# copy the repository to /opt/voting-bot, then:
cd /opt/voting-bot
install -d -m 750 -o votebot -g votebot data
UV_PYTHON_PREFERENCE=only-system uv sync --frozen --no-dev
# token: a file readable only by votebot
umask 077; cat > bot_token; chown votebot:votebot bot_token
# .env from .env.example, owned root:votebot, mode 640
install -m 644 deploy/voting-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now voting-bot
journalctl -u voting-bot -f
```

`UV_PYTHON_PREFERENCE=only-system` makes the venv use the system Python. A Python that uv downloads lives in root's home, which the service cannot read.

## Test run

To test before the real date, set `start` and `end` to a short window, restart the bot and vote. Then stop the bot, delete the test votes and restore the real times:

```sh
systemctl stop voting-bot
set -a; . ./.env; set +a
.venv/bin/voting-bot reset-votes --yes   # registrations stay
systemctl start voting-bot
```

## License

MIT
