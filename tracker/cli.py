"""Command line: python -m tracker <command>   (or ./tracker.sh <command>).

Typical routine run:
    ./tracker.sh brief            -> research brief for the LLM
    (LLM researches and writes findings.json)
    ./tracker.sh ingest findings.json   -> diff, Telegram alerts, sheet views
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .brief import build as build_brief
from .engine import Engine, prune
from .messages import Message, esc, render
from .model import State
from .normalize import TZ, is_past
from .store import FileStore, SheetStore
from .telegram import Telegram, TelegramError
from .watchlist import Watchlist

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / ".tracker"
LAST_BRIEF = WORK_DIR / "last_brief.json"
DEFAULT_SEED = Path(__file__).resolve().parent / "data" / "seed_nl.json"


class Ctx:
    def __init__(self, args):
        self.args = args
        if getattr(args, "now", None):
            now = datetime.fromisoformat(args.now)
            self.now = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
        else:
            self.now = datetime.now(TZ)
        self.dry = bool(getattr(args, "dry_run", False))
        self.ss = None
        if args.state:
            self.store = FileStore(args.state)
        else:
            from .sheets import open_spreadsheet
            self.ss = open_spreadsheet()
            self.store = SheetStore(self.ss)
        self.telegram = None if args.no_telegram else Telegram.from_env()
        self._wl = None

    @property
    def watchlist(self) -> Watchlist:
        if self._wl is None:
            from . import sheets
            if self.args.artists_file:
                artists = sheets.read_watchlist_file(self.args.artists_file)
            elif self.ss is not None:
                artists = sheets.read_watchlist(self.ss)
            else:
                sys.exit("error: no watchlist - pass --artists-file or configure the Google Sheet")
            if not artists:
                sys.exit("error: the watchlist is empty")
            self._wl = Watchlist(artists)
        return self._wl

    def stamp(self) -> str:
        return self.now.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def _print_block(title: str, messages: list[Message]):
    if not messages:
        return
    print(f"\n{title}")
    for m in messages:
        print("-" * 60)
        print(m.text)
    print("-" * 60)


def deliver(ctx: Ctx, st: State, messages: list[Message]) -> dict:
    """Queue messages in the state, save, then send everything pending.
    Saving first means a crash can at worst re-send, never lose an alert."""
    if ctx.dry:
        _print_block(f"[dry run] {len(messages)} message(s) would be sent:", messages)
        return {"sent": 0, "printed": 0, "queued": 0, "handoff": 0, "error": ""}
    for m in messages:
        st.pending.append({"created": ctx.stamp(), "html": m.html, "keys": m.keys, "loud": m.loud,
                           "kinds": m.kinds, "artist": m.artist})
    if messages:
        ctx.store.save(st)
    return flush(ctx, st)


def flush(ctx: Ctx, st: State) -> dict:
    """Send everything pending. Without Telegram credentials the alerts are printed
    (for the routine's chat reply) - or, with TRACKER_DELIVERY=queue, left queued
    for the scheduled GitHub Actions sender, which has the credentials."""
    handoff = ctx.telegram is None and os.environ.get("TRACKER_DELIVERY", "").strip().lower() == "queue"
    sent, printed, failed, remaining, error = [], [], [], [], ""
    for p in st.pending:
        if p.get("keys") and all(k in st.sent for k in p["keys"]):
            continue
        msg = Message(p["html"], p.get("keys", []), p.get("loud", True), p.get("kinds", []),
                      p.get("artist", ""))
        if handoff:
            remaining.append(p)
            continue
        if ctx.telegram is None:
            printed.append(msg)
            via = "stdout"
        elif error:
            remaining.append(p)
            failed.append(msg)
            continue
        else:
            try:
                ctx.telegram.send(msg.html, silent=not msg.loud)
                sent.append(msg)
                via = "telegram"
                time.sleep(0.4)
            except TelegramError as exc:
                error = str(exc)
                p["error"] = error
                remaining.append(p)
                failed.append(msg)
                continue
        for k in msg.keys:
            st.sent[k] = f"{ctx.stamp()} {via}"
    st.pending = remaining
    if sent or printed or failed:
        ctx.store.save(st)
    if ctx.ss is not None and (sent or printed):
        try:
            from .sheets import alert_log_row, log_alerts
            log_alerts(ctx.ss, [alert_log_row(ctx.now, "telegram", m) for m in sent]
                       + [alert_log_row(ctx.now, "chat", m) for m in printed])
        except Exception as exc:          # the log is cosmetic; never fail a run on it
            print(f"warning: could not append to the alert log: {exc}")
    if sent:
        print(f"\nTelegram: sent {len(sent)} message(s).")
        for m in sent:
            print(f"  - {m.text.splitlines()[0]}")
    _print_block("TELEGRAM IS NOT CONFIGURED - relay these alerts to the user in your final reply:", printed)
    if failed:
        print(f"\nTELEGRAM DELIVERY FAILED ({error}). {len(failed)} message(s) queued for retry on "
              "the next run. Tell the user, and include these alerts in your final reply:")
        _print_block("", failed)
    if handoff and remaining:
        print(f"\n{len(remaining)} alert(s) queued for the scheduled Telegram sender (TRACKER_DELIVERY=queue).")
    return {"sent": len(sent), "printed": len(printed), "queued": len(failed), "error": error,
            "handoff": len(remaining) if handoff else 0}


def _update_views(ctx: Ctx, st: State):
    if ctx.ss is None or ctx.dry:
        return
    try:
        from .sheets import write_shows
        write_shows(ctx.ss, st, ctx.now.date())
    except Exception as exc:
        print(f"warning: could not update the 'NL Shows' tab: {exc}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"error: cannot read {path}: {exc}")


def bootstrap(ctx: Ctx, st: State, seeds: list[str], legacy: bool) -> dict:
    """Record everything the user has already been told about, without alerting."""
    counts = {}
    for path in seeds:
        if path and os.path.exists(path):
            data = _load_json(path)
            res = Engine(st, ctx.watchlist, ctx.now, origin="seed", baseline=True).ingest(data)
            counts[f"seed {os.path.basename(path)}"] = len(res.created)
    if legacy:
        rows = []
        if ctx.args.legacy_csv:
            import csv
            from .sheets import legacy_rows_to_findings
            with open(ctx.args.legacy_csv, encoding="utf-8") as fh:
                rows = legacy_rows_to_findings(list(csv.reader(fh)), nl_only=False)
        elif ctx.ss is not None:
            from .sheets import read_legacy
            rows = read_legacy(ctx.ss)
        if rows:
            res = Engine(st, ctx.watchlist, ctx.now, origin="legacy", baseline=True).ingest({"shows": rows})
            counts["legacy rows"] = len(rows)
            counts["legacy shows"] = len(res.created)
    st.initialized = ctx.stamp()
    return counts


def cmd_bootstrap(ctx: Ctx):
    st = ctx.store.load()
    if st.initialized and not ctx.args.force:
        sys.exit(f"state already initialized at {st.initialized} (use --force to re-import)")
    counts = bootstrap(ctx, st, ctx.args.seed or [str(DEFAULT_SEED)], not ctx.args.no_legacy)
    upcoming = sum(1 for s in st.shows.values() if not is_past(s.date, ctx.now.date()))
    print(f"Initialized: {counts}; {upcoming} upcoming NL shows known; "
          f"{len(st.sent)} alert keys marked as already sent.")
    if not ctx.dry:
        ctx.store.save(st)
        _update_views(ctx, st)


def cmd_brief(ctx: Ctx):
    st = ctx.store.load()
    text, meta = build_brief(st, ctx.watchlist, ctx.now, batch_size=ctx.args.batch)
    if not st.initialized:
        text += ("\nNOTE: the tracker state is not initialized yet. The first ingest imports what "
                 "the user already knows and runs as a silent baseline (no alerts).\n")
    WORK_DIR.mkdir(exist_ok=True)
    LAST_BRIEF.write_text(json.dumps(meta), encoding="utf-8")
    if ctx.args.out:
        Path(ctx.args.out).write_text(text, encoding="utf-8")
        print(f"Brief written to {ctx.args.out} ({len(text.splitlines())} lines).")
    else:
        print(text)


def _default_checked(findings: dict, today: str) -> list[str]:
    try:
        meta = json.loads(LAST_BRIEF.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return meta.get("hot", []) + meta.get("batch", []) if meta.get("date") == today else []


def cmd_ingest(ctx: Ctx):
    findings = _load_json(ctx.args.findings)
    if isinstance(findings, list):
        findings = {"shows": findings}
    if not isinstance(findings, dict):
        sys.exit("error: findings must be a JSON object with 'shows', 'checked', 'news'")
    if not findings.get("checked") and ctx.args.origin == "agent":
        findings["checked"] = _default_checked(findings, ctx.now.date().isoformat())
    process(ctx, ctx.store.load(), findings, ctx.args.origin)


def cmd_poll_ticketmaster(ctx: Ctx):
    from .ticketmaster import Ticketmaster, poll
    key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
    if not key:
        sys.exit("error: TICKETMASTER_API_KEY is not set")
    st = ctx.store.load()
    tm = Ticketmaster(key)
    shows, errors = poll(tm, ctx.watchlist, st.extra.setdefault("ticketmaster", {}), ctx.now)
    print(f"Ticketmaster: {len(shows)} NL event(s) for watchlist artists ({tm.calls} API calls).")
    for e in errors[:10]:
        print(f"  warning: {e}")
    if errors and not shows and len(errors) >= min(3, len(ctx.watchlist.names)):
        sys.exit("error: Ticketmaster polling failed - nothing ingested")
    process(ctx, st, {"shows": shows}, "ticketmaster")


def process(ctx: Ctx, st: State, findings: dict, origin: str):
    """Merge findings, add due reminders / the weekly digest, deliver, update views."""
    baseline = not st.initialized
    if baseline:
        counts = bootstrap(ctx, st, [str(DEFAULT_SEED)], legacy=True)
        print(f"First run: imported what you already know ({counts}); this run is a silent baseline.")

    eng = Engine(st, ctx.watchlist, ctx.now, origin=origin, baseline=baseline)
    res = eng.ingest(findings)
    if not baseline:
        eng.reminders()
        eng.digest()
    messages = render(eng.alerts, st, ctx.now)
    if baseline:
        upcoming = sum(1 for s in st.shows.values() if not s.dismissed and not is_past(s.date, ctx.now.date()))
        key = f"init|{ctx.now.date().isoformat()}"
        if key not in st.sent:
            messages = [Message(
                f"✅ <b>NL concert tracker is live</b>\nWatching {len(ctx.watchlist.names)} artists; "
                f"{upcoming} upcoming NL shows already known.\nFrom now on you only get news: new shows, "
                "registrations, lotteries, presales, sales, sold-outs and cancellations.",
                [key], loud=False, kinds=["init"])]
    st.runs.append({"at": ctx.stamp(), "kind": f"ingest:{origin}", "checked": len(res.checked),
                    "findings": len(findings.get("shows") or []), "created": len(res.created),
                    "alerts": len(eng.alerts), "baseline": baseline})
    prune(st, ctx.now.date())

    print(f"Ingested {len(findings.get('shows') or [])} show report(s) for {len(res.checked)} checked "
          f"artist(s): {len(res.created)} new, {len(res.updated)} updated, {res.unchanged} unchanged, "
          f"{res.skipped_past} past (ignored).")
    if res.warnings:
        print("Warnings:")
        for w in res.warnings:
            print(f"  - {w}")
    report = deliver(ctx, st, messages)
    if not ctx.dry and not messages:
        ctx.store.save(st)            # merged shows + rotation, even on a silent day
    _update_views(ctx, st)
    total = report["sent"] + report["printed"]
    if ctx.dry:
        print(f"\nSUMMARY: dry run - {len(messages)} alert(s) computed, nothing sent or saved.")
    elif baseline:
        print("\nSUMMARY: baseline run - state initialized, no alerts sent.")
    elif report["handoff"]:
        print(f"\nSUMMARY: {report['handoff']} alert(s) queued for the scheduled Telegram sender.")
    else:
        print(f"\nSUMMARY: {total} alert(s) delivered"
              + (f", {report['queued']} queued (Telegram failed: {report['error']})" if report["queued"] else "")
              + ("." if total or report["queued"] else " - nothing new today."))


def cmd_remind(ctx: Ctx):
    st = ctx.store.load()
    if not st.initialized:
        print("State not initialized yet (the first routine run does that) - nothing to remind.")
        return
    eng = Engine(st, ctx.watchlist, ctx.now)
    eng.reminders()
    if not ctx.args.no_digest:
        eng.digest()
    pruned = prune(st, ctx.now.date())
    report = deliver(ctx, st, render(eng.alerts, st, ctx.now))
    if not ctx.dry and not eng.alerts and pruned:
        ctx.store.save(st)
        _update_views(ctx, st)
    print(f"Reminders: {len(eng.alerts)} due, {report['sent']} sent, {report['queued']} queued.")


def cmd_flush(ctx: Ctx):
    st = ctx.store.load()
    report = flush(ctx, st) if not ctx.dry else {}
    print(f"Pending messages flushed: {report}")


def cmd_status(ctx: Ctx):
    st = ctx.store.load()
    today = ctx.now.date()
    upcoming = [s for s in st.shows.values() if not s.dismissed and not is_past(s.date, today)]
    print(f"State: {ctx.store.describe()} rev {st.rev}, initialized {st.initialized or 'never'}")
    print(f"Shows: {len(upcoming)} upcoming ({sum(not s.verified for s in upcoming)} unverified, "
          f"{sum(1 for s in st.shows.values() if s.dismissed)} dismissed)")
    print(f"Ledger: {len(st.sent)} keys; pending messages: {len(st.pending)}; news items: {len(st.news)}")
    print(f"Telegram: {'configured' if ctx.telegram else 'NOT configured (alerts go to stdout)'}")
    for r in st.runs[-7:]:
        print(f"  run {r}")
    if ctx.args.artists_file or ctx.ss is not None:
        wl = ctx.watchlist
        never = [n for n in wl.names if not st.artists.get(n, {}).get("last_checked")]
        print(f"Watchlist: {len(wl.names)} artists, never checked: {', '.join(never) or 'none'}")


def cmd_shows(ctx: Ctx):
    from .sheets import shows_table
    st = ctx.store.load()
    for row in shows_table(st, ctx.now.date()):
        print(" | ".join(row[:7]))


def _edit_show(ctx: Ctx, fn):
    st = ctx.store.load()
    show = st.shows.get(ctx.args.id)
    if not show:
        sys.exit(f"unknown show id {ctx.args.id!r} (see: shows)")
    fn(show)
    show.log(ctx.now.date().isoformat(), f"manual: {ctx.args.cmd}")
    if not ctx.dry:
        ctx.store.save(st)
        _update_views(ctx, st)
    print(f"{ctx.args.cmd}: {show.id}")


def cmd_test_telegram(ctx: Ctx):
    if ctx.telegram is None:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")
    try:
        ctx.telegram.send(f"🔔 Test from the NL concert tracker ({esc(ctx.now.strftime('%a %-d %b %H:%M'))}).")
    except TelegramError as exc:
        sys.exit(f"Telegram test failed: {exc}")
    print("Telegram test message sent.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracker", description="NL concert tracker")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--state", help="local state JSON file instead of the Google Sheet")
    p.add_argument("--artists-file", help="local watchlist file instead of the sheet's Artists tab")
    p.add_argument("--now", help="pretend it is this Amsterdam time (testing)")
    p.add_argument("--no-telegram", action="store_true", help="print alerts instead of sending")
    p.add_argument("--dry-run", action="store_true", help="compute and print, never send or save")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("brief", help="print the research brief for this run")
    b.add_argument("--batch", type=int, default=15, help="rotation batch size (default 15)")
    b.add_argument("--out", help="write the brief to this file")

    i = sub.add_parser("ingest", help="merge findings.json, send alerts, update the sheet")
    i.add_argument("findings")
    i.add_argument("--origin", default="agent")

    r = sub.add_parser("remind", help="send due reminders + weekly digest, retry pending")
    r.add_argument("--no-digest", action="store_true")

    sub.add_parser("poll-ticketmaster", help="ingest NL Ticketmaster events (TICKETMASTER_API_KEY)")

    sub.add_parser("flush", help="retry undelivered messages")
    sub.add_parser("status", help="state summary")
    sub.add_parser("shows", help="list upcoming shows")

    bs = sub.add_parser("bootstrap", help="initialize state from seed + old tracker tabs (no alerts)")
    bs.add_argument("--seed", action="append", help="seed findings JSON (repeatable)")
    bs.add_argument("--no-legacy", action="store_true", help="don't import the old 'Schedule' tabs")
    bs.add_argument("--legacy-csv", help="import old rows from a CSV instead of the sheet")
    bs.add_argument("--force", action="store_true")

    for name, help_ in (("dismiss", "mark a known show as bogus"), ("mute", "stop alerts for a show"),
                        ("unmute", "resume alerts for a show")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("id")
        if name == "dismiss":
            c.add_argument("--reason", default="dismissed manually")

    sub.add_parser("test-telegram", help="send a test message")

    args = p.parse_args(argv)
    if not hasattr(args, "legacy_csv"):
        args.legacy_csv = None
    ctx = Ctx(args)
    if args.cmd == "brief":
        cmd_brief(ctx)
    elif args.cmd == "ingest":
        cmd_ingest(ctx)
    elif args.cmd == "remind":
        cmd_remind(ctx)
    elif args.cmd == "poll-ticketmaster":
        cmd_poll_ticketmaster(ctx)
    elif args.cmd == "flush":
        cmd_flush(ctx)
    elif args.cmd == "status":
        cmd_status(ctx)
    elif args.cmd == "shows":
        cmd_shows(ctx)
    elif args.cmd == "bootstrap":
        cmd_bootstrap(ctx)
    elif args.cmd == "dismiss":
        from .engine import dismiss_show
        _edit_show(ctx, lambda s: dismiss_show(s, args.reason, ctx.now.date().isoformat()))
    elif args.cmd == "mute":
        _edit_show(ctx, lambda s: setattr(s, "muted", True))
    elif args.cmd == "unmute":
        _edit_show(ctx, lambda s: setattr(s, "muted", False))
    elif args.cmd == "test-telegram":
        cmd_test_telegram(ctx)


if __name__ == "__main__":
    main()
