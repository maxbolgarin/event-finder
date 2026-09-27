from tracker.engine import Engine
from tracker.messages import plain, render

from .conftest import at

TM = "https://www.ticketmaster.nl/artist/muse-tickets/26326"


def _render(runner, when, findings, reminders=False):
    r = runner.run(when, findings, reminders=reminders)
    return render(r.alerts, runner.state, at(when))


def test_multi_night_new_show_is_one_message(runner):
    msgs = _render(runner, "2026-09-27T09:00", {"shows": [
        {"artist": "Muse", "date": d, "venue": "Ziggo Dome", "city": "Amsterdam", "url": TM,
         "sales": [{"type": "presale", "name": "Muse fan presale", "start": "2026-10-01T10:00"},
                   {"type": "general", "start": "2026-10-03T10:00"}]}
        for d in ("2026-11-29", "2026-11-30")]})
    assert len(msgs) == 1
    text = msgs[0].text
    assert "🆕 Muse — new NL shows" in text
    assert "Sun 29 Nov 2026 · Mon 30 Nov 2026" in text
    assert "Presale (Muse fan presale): Thu 1 Oct 10:00" in text
    assert text.count("Presale") == 1                     # shared window listed once
    assert "General sale: Sat 3 Oct 10:00" in text
    assert TM in text
    assert set(msgs[0].keys) >= {"new|muse-2026-11-29", "new|muse-2026-11-30"}


def test_festival_message(runner):
    (msg,) = _render(runner, "2026-11-20T09:00", {"shows": [
        {"artist": "Architects", "date": "2027", "festival": "Pinkpop", "url": "https://www.pinkpop.nl/x"}]})
    assert msg.text.startswith("🆕 Architects — confirmed for Pinkpop 2027")


def test_registration_message(runner):
    runner.run("2026-09-01T09:00", {"shows": [{"artist": "Oasis", "date": "2027-07-16",
                                               "venue": "Johan Cruijff ArenA", "url": "https://oasisinet.com/"}]})
    (msg,) = _render(runner, "2026-09-10T09:30", {"shows": [{
        "artist": "Oasis", "date": "2027-07-16", "venue": "Johan Cruijff ArenA",
        "sales": [{"type": "registration", "start": "2026-09-10T09:00", "end": "2026-09-17T17:00",
                   "url": "https://oasisinet.com/register"}]}]})
    assert msg.text.splitlines()[0] == "📝 Oasis — registration is open"
    assert "Registration: Thu 10 Sep 09:00 → Thu 17 Sep 17:00" in msg.text
    assert "https://oasisinet.com/register" in msg.text and msg.loud


def test_reminder_groups_nights(runner):
    runner.run("2026-09-20T09:00", {"shows": [
        {"artist": "Muse", "date": d, "venue": "Ziggo Dome", "url": TM,
         "sales": [{"type": "presale", "start": "2026-10-01T10:00"}]} for d in ("2026-11-29", "2026-11-30")]})
    eng = Engine(runner.state, runner.wl, at("2026-09-30T12:00"))
    (msg,) = render(eng.reminders(), runner.state, at("2026-09-30T12:00"))
    assert msg.text.splitlines()[0] == "⏰ Muse — presale opens tomorrow 10:00"
    assert "Sun 29 Nov 2026 · Mon 30 Nov 2026" in msg.text


def test_sold_out_is_silent(runner):
    runner.run("2026-09-20T09:00", {"shows": [{"artist": "Muse", "date": "2026-11-29", "venue": "Ziggo Dome",
                                               "url": TM, "status": "on sale"}]})
    (msg,) = _render(runner, "2026-09-21T09:00", {"shows": [{"artist": "Muse", "date": "2026-11-29",
                                                             "status": "sold out"}]})
    assert "sold out" in msg.text and not msg.loud


def test_html_is_escaped_and_plain_text_readable(runner):
    (msg,) = _render(runner, "2026-09-27T09:00", {"shows": [
        {"artist": "Korn", "lineup": "Korn <b>&</b> friends", "date": "2026-11-08", "venue": "Ziggo Dome",
         "url": "https://www.ticketmaster.nl/x?a=1&b=2"}]})
    assert "&lt;b&gt;" in msg.html and 'href="https://www.ticketmaster.nl/x?a=1&amp;b=2"' in msg.html
    assert "Korn <b>&</b> friends" in plain(msg.html)
