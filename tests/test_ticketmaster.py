import json

import pytest

from tracker import cli
from tracker.engine import Engine
from tracker.store import FileStore
from tracker.ticketmaster import Ticketmaster, event_to_finding, poll
from tracker.watchlist import Watchlist

from .conftest import at


def event(eid="Z1", date="2026-11-29", attractions=("Muse",), code="onsale", public="2026-10-03T08:00:00Z",
          presales=(("Live Nation Presale", "2026-10-01T08:00:00Z", "2026-10-02T21:00:00Z"),),
          venue="Ziggo Dome", city="Amsterdam"):
    return {
        "id": eid, "name": " & ".join(attractions), "url": f"https://www.ticketmaster.nl/event/{eid}",
        "sales": {"public": {"startDateTime": public, "startTBD": False, "startTBA": False},
                  "presales": [{"name": n, "startDateTime": s, "endDateTime": e} for n, s, e in presales]},
        "dates": {"start": {"localDate": date, "localTime": "20:00:00", "dateTBA": False, "dateTBD": False},
                  "timezone": "Europe/Amsterdam", "status": {"code": code}},
        "_embedded": {
            "venues": [{"name": venue, "city": {"name": city}, "country": {"countryCode": "NL"}}],
            "attractions": [{"name": a, "id": f"K{a}"} for a in attractions]},
    }


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class FakeAPI:
    """Serves attractions.json / events.json like the Discovery API."""

    def __init__(self, attractions, events, status=200):
        self.attractions, self.events, self.status, self.calls = attractions, events, status, []

    def get(self, url, params, timeout):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params)))
        if self.status != 200:
            return Resp(self.status, {"fault": "Invalid ApiKey"})
        if url.endswith("attractions.json"):
            atts = [a for a in self.attractions if params["keyword"].lower() in a["name"].lower()]
            return Resp(200, {"_embedded": {"attractions": atts}} if atts else {})
        evs = self.events.get(params["attractionId"], [])
        return Resp(200, {"_embedded": {"events": evs}} if evs else {})


WL = Watchlist.from_names(["Muse", "Korn", "Architects", "Placebo"])


def test_event_to_finding_converts_times_and_windows():
    f = event_to_finding(event(), WL, at("2026-09-27T12:00"))
    assert f["artist"] == "Muse" and f["date"] == "2026-11-29" and f["venue"] == "Ziggo Dome"
    assert f["sales"] == [
        {"type": "Live Nation Presale", "name": "Live Nation Presale", "start": "2026-10-01T10:00",
         "end": "2026-10-02T23:00"},
        {"type": "general", "start": "2026-10-03T10:00"}]
    assert f["status"] == "announced"                      # public sale hasn't started yet
    assert event_to_finding(event(), WL, at("2026-10-01T12:00"))["status"] == "presale"
    assert event_to_finding(event(), WL, at("2026-10-04T12:00"))["status"] == "on_sale"
    assert event_to_finding(event(code="cancelled"), WL, at("2026-10-04T12:00"))["status"] == "cancelled"
    assert event_to_finding(event(attractions=("Museum Night",)), WL, at("2026-09-27T12:00")) is None


def test_multi_attraction_event_keeps_the_bill():
    f = event_to_finding(event(attractions=("Korn", "Architects")), WL, at("2026-09-27T12:00"))
    assert f["artists"] == ["Korn", "Architects"] and f["lineup"] == "Korn + Architects"


def test_poll_caches_attraction_ids_and_feeds_the_engine(state):
    api = FakeAPI(
        attractions=[{"name": "Muse", "id": "KMuse", "upcomingEvents": {"_total": 30}},
                     {"name": "Muse Tribute Band", "id": "KTribute", "upcomingEvents": {"_total": 9}}],
        events={"KMuse": [event("A", "2026-11-29"), event("B", "2026-11-30")]})
    tm = Ticketmaster("KEY", session=api, sleep=lambda s: None)
    cache = {}
    findings, errors = poll(tm, Watchlist.from_names(["Muse"]), cache, at("2026-09-27T12:00"))
    assert errors == [] and len(findings) == 2
    assert cache["attractions"]["Muse"]["ids"] == ["KMuse"]            # the tribute band is ignored
    calls_first = len(api.calls)
    poll(tm, Watchlist.from_names(["Muse"]), cache, at("2026-09-28T12:00"))
    assert len(api.calls) - calls_first == 1                         # cached id: events call only

    eng = Engine(state, Watchlist.from_names(["Muse"]), at("2026-09-27T12:00"), origin="ticketmaster")
    eng.ingest({"shows": findings})
    assert sorted(a.kind for a in eng.alerts) == ["new", "new"]


def test_bad_key_stops_early_without_leaking_it():
    api = FakeAPI([], {}, status=401)
    tm = Ticketmaster("SUPERSECRET", session=api, sleep=lambda s: None)
    findings, errors = poll(tm, WL, {}, at("2026-09-27T12:00"))
    assert findings == [] and len(errors) == 1 and "SUPERSECRET" not in errors[0]


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    (tmp_path / "artists.txt").write_text("Muse\nPlacebo\n")
    (tmp_path / "seed.json").write_text(json.dumps({"shows": []}))
    monkeypatch.setattr(cli, "DEFAULT_SEED", tmp_path / "seed.json")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)

    def run(*args, now):
        cli.main(["--state", str(tmp_path / "state.json"), "--artists-file", str(tmp_path / "artists.txt"),
                  "--now", now, *args])
    run.path = tmp_path
    return run


def test_poll_command_and_queue_handoff(cli_env, capsys, monkeypatch):
    api = FakeAPI([{"name": "Muse", "id": "KMuse", "upcomingEvents": {"_total": 3}}],
                  {"KMuse": [event("A", "2026-11-29")]})
    real_init = Ticketmaster.__init__
    monkeypatch.setattr(Ticketmaster, "__init__",
                        lambda self, key, **kw: real_init(self, key, session=api, sleep=lambda s: None))
    monkeypatch.setenv("TICKETMASTER_API_KEY", "KEY")
    cli_env("poll-ticketmaster", now="2026-09-27T12:00")             # first run: baseline
    assert "baseline run" in capsys.readouterr().out

    api.events["KMuse"].append(event("B", "2026-11-30"))
    monkeypatch.setenv("TRACKER_DELIVERY", "queue")                   # routine without Telegram access
    cli_env("poll-ticketmaster", now="2026-09-29T12:00")
    out = capsys.readouterr().out
    assert "queued for the scheduled Telegram sender" in out
    st = FileStore(str(cli_env.path / "state.json")).load()
    assert len(st.pending) == 1 and "new|muse-2026-11-30" in st.pending[0]["keys"]
    assert "new|muse-2026-11-30" not in st.sent

    sent = []
    monkeypatch.delenv("TRACKER_DELIVERY")
    monkeypatch.setattr(cli.Telegram, "from_env", classmethod(lambda cls: type(
        "T", (), {"send": lambda self, html, silent=False: sent.append(html)})()))
    cli_env("remind", "--no-digest", now="2026-09-29T12:30")          # the scheduled sender
    st = FileStore(str(cli_env.path / "state.json")).load()
    assert st.pending == [] and len(sent) == 1 and "Mon 30 Nov 2026" in sent[0]
