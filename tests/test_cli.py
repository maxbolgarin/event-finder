"""End-to-end runs of the CLI against a local state file (no Google, no network)."""

import json

import pytest

from tracker import cli
from tracker.store import FileStore
from tracker.telegram import TelegramError

FINDINGS = {
    "checked": ["Muse", "Radiohead"],
    "shows": [{"artist": "Muse", "date": "2026-11-29", "venue": "Ziggo Dome", "city": "Amsterdam",
               "url": "https://www.ticketmaster.nl/artist/muse-tickets/26326",
               "sales": [{"type": "presale", "start": "2026-10-01T10:00"}]}],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "artists.txt").write_text("Muse\nRadiohead\nOasis\nCurrents | | US metalcore band\n")
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"shows": [{"artist": "Oasis", "date": "2027-07-16",
                                           "venue": "Johan Cruijff ArenA", "status": "presale"}]}))
    monkeypatch.setattr(cli, "DEFAULT_SEED", seed)
    monkeypatch.setattr(cli, "WORK_DIR", tmp_path / ".tracker")
    monkeypatch.setattr(cli, "LAST_BRIEF", tmp_path / ".tracker" / "last_brief.json")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    def run(*args, now="2026-09-27T09:00"):
        cli.main(["--state", str(tmp_path / "state.json"), "--artists-file", str(tmp_path / "artists.txt"),
                  "--now", now, *args])

    def write(name, data):
        (tmp_path / name).write_text(json.dumps(data))
        return str(tmp_path / name)

    run.write, run.path = write, tmp_path
    return run


def test_first_run_is_a_silent_baseline_then_alerts(env, capsys):
    env("brief", "--out", str(env.path / "brief.md"))
    brief = (env.path / "brief.md").read_text()
    assert "Muse" in brief and "US metalcore band" in brief and "not initialized" in brief

    env("ingest", env.write("f1.json", FINDINGS))
    out = capsys.readouterr().out
    assert "baseline run" in out and "NL concert tracker is live" in out
    st = FileStore(str(env.path / "state.json")).load()
    assert st.initialized and "oasis-2027-07-16" in st.shows and "muse-2026-11-29" in st.shows
    assert st.artists["Muse"]["last_checked"] == "2026-09-27"

    second = {"checked": ["Muse"], "shows": [
        {**FINDINGS["shows"][0], "sales": [{"type": "presale", "start": "2026-10-01T10:00"},
                                           {"type": "general", "start": "2026-10-03T10:00"}]},
        {"artist": "Muse", "date": "2026-11-30", "venue": "Ziggo Dome",
         "url": "https://www.ticketmaster.nl/artist/muse-tickets/26326"}]}
    env("ingest", env.write("f2.json", second), now="2026-09-29T09:00")
    out = capsys.readouterr().out
    assert "TELEGRAM IS NOT CONFIGURED" in out
    assert "🎫 Muse — general sale announced" in out and "🆕 Muse — new NL show" in out
    assert "SUMMARY: 2 alert(s) delivered" in out

    env("ingest", env.write("f3.json", second), now="2026-09-29T18:00")
    assert "nothing new today" in capsys.readouterr().out


def test_failed_telegram_queues_and_retries(env, capsys, monkeypatch):
    env("ingest", env.write("f1.json", FINDINGS))            # baseline
    capsys.readouterr()

    class Broken:
        def send(self, html, silent=False):
            raise TelegramError("api.telegram.org is blocked by this environment's network policy")

    class Working:
        sent = []

        def send(self, html, silent=False):
            self.sent.append(html)

    monkeypatch.setattr(cli.Telegram, "from_env", classmethod(lambda cls: Broken()))
    new = {"checked": ["Radiohead"], "shows": [{"artist": "Radiohead", "date": "2027-05-01",
                                                "venue": "Ziggo Dome", "url": "https://www.radiohead.com/x"}]}
    env("ingest", env.write("f2.json", new), now="2026-09-29T09:00")
    out = capsys.readouterr().out
    assert "TELEGRAM DELIVERY FAILED" in out and "network policy" in out and "1 queued" in out
    assert len(FileStore(str(env.path / "state.json")).load().pending) == 1

    env("ingest", env.write("f3.json", new), now="2026-09-29T10:00")   # still broken: no duplicate queued
    capsys.readouterr()
    assert len(FileStore(str(env.path / "state.json")).load().pending) == 1

    monkeypatch.setattr(cli.Telegram, "from_env", classmethod(lambda cls: Working()))
    env("flush", now="2026-09-29T11:00")
    st = FileStore(str(env.path / "state.json")).load()
    assert st.pending == [] and len(Working.sent) == 1 and "Radiohead" in Working.sent[0]
    assert st.sent["new|radiohead-2027-05-01"].endswith("telegram")


def test_dry_run_changes_nothing(env, capsys):
    env("--dry-run", "ingest", env.write("f1.json", FINDINGS))
    assert not (env.path / "state.json").exists()
    assert "dry run" in capsys.readouterr().out


def test_remind_and_manual_controls(env, capsys):
    env("ingest", env.write("f1.json", FINDINGS))            # baseline incl. presale 1 Oct 10:00
    capsys.readouterr()
    env("remind", now="2026-09-30T12:00")
    assert "⏰ Muse — presale opens tomorrow 10:00" in capsys.readouterr().out
    env("mute", "muse-2026-11-29")
    env("remind", now="2026-10-01T09:10")
    assert "opens in" not in capsys.readouterr().out
    env("dismiss", "oasis-2027-07-16", "--reason", "test")
    assert FileStore(str(env.path / "state.json")).load().shows["oasis-2027-07-16"].dismissed == "test"
