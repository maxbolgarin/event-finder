# event-finder

Watches concert news in the Netherlands for the artists in my Google Sheet and
sends **only new, actionable news** to Telegram: new shows and extra dates,
registrations / verified-fan sign-ups, lotteries, presales, general sales,
sold-outs, cancellations - plus reminders right before a sale opens.
If nothing changed, it stays silent.

## How it works

```
Claude routine (daily)                    Google Sheet (private)             Telegram
./tracker.sh brief  ------- reads ------> Artists tab (watchlist)
   research brief: today's artists,       _tracker_state (hidden memory)
   known shows with IDs, sweeps
web search -> findings.json
./tracker.sh ingest ------- diff -------> state, "NL Shows", "NL Alerts" --> one message per real change

GitHub Action (optional, every 30 min): Ticketmaster poll + reminders + queued alerts --> Telegram
```

The LLM is only a **sensor**: each run it reports every NL show it finds for the
artists it checked, known or new, with status and ticket windows. The script is
the **only judge** of what is new, and every alert has a ledger key that is sent
at most once.

**Why the old version re-sent things.** It de-duplicated on exact strings, so
"Dynamo Metalfest", "IJssportcentrum (Dynamo MetalFest)" and 11 other spellings
became 13 "new" Lamb of God shows. On top of that:
- the notification was the LLM's own chat summary;
- the model never saw what it had found before;
- status changes (presale announced, sold out) were dropped;
- past shows and resale / SEO-spam sites were accepted;
- the routine can search but can't open ticket sites.

**Identity rules** (`tracker/engine.py`):
- A show is *(artist, date)*; the venue spelling is just an attribute.
- A festival slot is *(artist, festival, year)*, so it survives an unknown day.
- Support acts merge into the headline show (Korn + Architects is one show), and a newly added support act is its own alert.
- Wrong-year duplicates are caught; shows backed only by resale/spam URLs stay "unverified" and silent until a real source confirms them.
- A step back in status (e.g. sold out -> on sale) must be reported on two different days, or explicitly as "extra tickets released".

| Alert | Example |
|---|---|
| 🆕 new show / festival slot | `🆕 Muse — new NL shows` · dates, venue, sale windows, link |
| 📝 🎲 🔐 🎫 ticket window announced | registration (with closing time), lottery / unique-code sale, presale, general sale |
| 🟢 sale live · 🔁 tickets back · ➕ support added · 📅 date moved · ❌ cancelled | |
| ⏰ / 🚨 / ⏳ reminders | "presale opens tomorrow 10:00", "opens in 40 min", "registration closes today 17:00" |
| 🔴 sold out · 🟠 few left · 📋 weekly check-in | sent silently |

## Setup

1. **Telegram.** Reuse the bot the Gumloop agent uses (same `TELEGRAM_BOT_TOKEN` /
   `TELEGRAM_CHAT_ID`), or create one with @BotFather, message it once, and read
   the chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. **Claude Code environment** (the one the routine runs in; it already has
   `GOOGLE_SA_JSON` and `SHEET_ID`):
   - add the env vars `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`;
   - under *Network access*, allow `api.telegram.org` (it is blocked today).
     Alternative: don't touch the network settings, set `TRACKER_DELIVERY=queue`
     and enable the GitHub Action below, which then delivers the alerts.
   - check it with `./tracker.sh test-telegram`.
3. **Routine.** Use `prompt_nl.md` as the prompt, e.g. at 07:45 and 16:45
   Amsterdam time. Once Telegram works you can turn off the routine's own push
   notification.
4. **First run** imports what you already know without alerting: the old
   "Schedule NL" / "Schedule" tabs plus `tracker/data/seed_nl.json` (the Gumloop
   agent's known list). It then sends a single "✅ tracker is live" message.
5. **Optional GitHub Action** (`.github/workflows/tracker.yml`, free for public
   repos). Add the repo secrets `GOOGLE_SA_JSON`, `SHEET_ID`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, and optionally `TICKETMASTER_API_KEY` (free at
   developer.ticketmaster.com). Then set the repo variable `TRACKER_SCHEDULE=on`.
   It sends the last-hour reminders on time, delivers queued alerts, and polls
   Ticketmaster hourly for new NL shows with exact sale times.

## The sheet

| Tab | |
|---|---|
| `Artists` | the watchlist. A = name, B = aliases (optional, comma separated), C = note for the researcher (optional, e.g. "US metalcore band"). Every non-blank row counts. |
| `NL Shows` | generated overview of all known upcoming NL shows (rewritten each run) |
| `NL Alerts` | log of every alert sent |
| `_tracker_state` | hidden; the tracker's memory - don't edit |
| `Schedule`, `Festival Schedule`, `Log`, `Schedule NL` | the old weekly worldwide job (`prompt.md` + `concert_sheets.py`), unchanged |

## Commands

```
./tracker.sh brief [--out brief.md]    research brief for this run
./tracker.sh ingest findings.json       merge, alert, update the sheet
./tracker.sh remind                     due reminders, weekly digest, retry queued alerts
./tracker.sh poll-ticketmaster          Ticketmaster fast lane (needs TICKETMASTER_API_KEY)
./tracker.sh status | shows             what the tracker knows
./tracker.sh dismiss ID | mute ID | unmute ID
./tracker.sh test-telegram
```

`tracker.sh` builds its own virtualenv (the cloud image's system `cryptography`
is broken). For local experiments use `--state state.json --artists-file
artists.txt` (one artist per line) with `--no-telegram`, `--dry-run` or
`--now 2026-10-01T09:00`.

## Development

```
pip install -r requirements-dev.txt
python -m pytest
```

`tests/test_legacy_replay.py` replays the old tracker's real rows and checks
that every show is announced exactly once.
