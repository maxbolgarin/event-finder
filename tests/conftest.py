from datetime import datetime

import pytest

from tracker.engine import Engine
from tracker.model import State
from tracker.normalize import TZ
from tracker.watchlist import Artist, Watchlist

ARTISTS = ["Radiohead", "Oasis", "Muse", "Korn", "Architects", "Poppy", "Lamb of God",
           "Devin Townsend", "Noel Gallagher's High Flying Birds", "Liam Gallagher",
           "Twenty One Pilots", "Hayley Williams", "Currents", "Paramore", "Limp Bizkit",
           "Tom Odell", "Kodaline", "Nothing But Thieves", "Thirty Seconds To Mars", "Placebo",
           "Oxxxymiron", "A.N.I", "Audiofreq", "Tallah"]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


@pytest.fixture
def wl():
    return Watchlist([Artist(n) for n in ARTISTS])


@pytest.fixture
def state():
    return State(initialized="2026-09-01T00:00:00+02:00")


class Runner:
    """Runs successive 'days' against one state, recording delivered alerts."""

    def __init__(self, state, wl):
        self.state, self.wl = state, wl

    def run(self, when: str, findings: dict, reminders: bool = False):
        eng = Engine(self.state, self.wl, at(when))
        res = eng.ingest(findings)
        if reminders:
            eng.reminders()
        for a in eng.alerts:          # pretend delivery succeeded
            for k in a.keys:
                self.state.sent[k] = at(when).isoformat()
        res.alerts = list(eng.alerts)
        return res

    def remind(self, when: str):
        eng = Engine(self.state, self.wl, at(when))
        alerts = eng.reminders()
        for a in alerts:
            for k in a.keys:
                self.state.sent[k] = at(when).isoformat()
        return alerts


@pytest.fixture
def runner(state, wl):
    return Runner(state, wl)


def kinds(alerts):
    return sorted(a.kind for a in alerts)
