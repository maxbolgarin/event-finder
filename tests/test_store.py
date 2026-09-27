import os

import gspread
import pytest

from tracker.model import Show, State
from tracker.store import FileStore, SheetStore, StateCorrupted, decode, encode, merge_into


def _state(n_shows=3, rev=0):
    st = State(rev=rev, initialized="2026-09-27T12:00:00+02:00")
    for i in range(n_shows):
        st.shows[f"s{i}"] = Show(id=f"s{i}", artists=["Muse"], date=f"2026-11-{i + 1:02d}", venue="Ziggo Dome")
        st.sent[f"new|s{i}"] = "2026-09-27T12:00:00+02:00"
    return st


def test_encode_decode_roundtrip():
    st = _state(50)
    header, chunks = encode(st)
    back = decode(header, chunks)
    assert back.to_dict() == st.to_dict()


def test_decode_rejects_truncated_state():
    header, chunks = encode(_state(5))
    with pytest.raises(StateCorrupted):
        decode(header, [chunks[0][:-10]])


def test_merge_keeps_both_writers_ledgers():
    ours, theirs = _state(2, rev=5), _state(0, rev=6)
    theirs.sent["sale|x|presale|2026-10-01"] = "t"
    theirs.shows["x"] = Show(id="x", artists=["Korn"], date="2026-11-08")
    theirs.artists["Korn"] = {"last_checked": "2026-09-27"}
    ours.artists["Korn"] = {"last_checked": "2026-09-20"}
    merge_into(ours, theirs)
    assert "sale|x|presale|2026-10-01" in ours.sent and "x" in ours.shows
    assert ours.artists["Korn"]["last_checked"] == "2026-09-27" and ours.rev == 6


def test_file_store_merges_concurrent_writes(tmp_path):
    path = str(tmp_path / "state.json")
    a, b = FileStore(path), FileStore(path)
    FileStore(path).save(_state(1))
    sa, sb = a.load(), b.load()
    sa.sent["only-a"] = "1"
    a.save(sa)
    sb.sent["only-b"] = "1"
    b.save(sb)                                   # must not drop "only-a"
    final = FileStore(path).load()
    assert {"only-a", "only-b"} <= set(final.sent)


class FakeWorksheet:
    def __init__(self, title, rows=20):
        self.title, self.row_count, self.cells, self.hidden = title, rows, {}, False

    def col_values(self, col):
        out = [self.cells.get(r, "") for r in range(1, self.row_count + 1)]
        while out and not out[-1]:
            out.pop()
        return out

    def acell(self, label):
        return type("Cell", (), {"value": self.cells.get(int(label[1:]))})()

    def update(self, range_name, values, value_input_option=None):
        start = int(range_name.split(":")[0][1:])
        assert start + len(values) - 1 <= self.row_count, "write beyond grid"
        for i, row in enumerate(values):
            assert len(row[0]) <= 50_000, "cell too large for Google Sheets"
            self.cells[start + i] = row[0]

    def batch_clear(self, ranges):
        for rng in ranges:
            a, b = rng.split(":")
            for r in range(int(a[1:]), int(b[1:]) + 1):
                self.cells.pop(r, None)

    def add_rows(self, n):
        self.row_count += n

    def hide(self):
        self.hidden = True


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}

    def worksheet(self, title):
        if title not in self.sheets:
            raise gspread.WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        self.sheets[title] = FakeWorksheet(title, rows)
        return self.sheets[title]


def test_sheet_store_roundtrip_chunks_and_hides():
    ss = FakeSpreadsheet()
    store = SheetStore(ss)
    assert store.load().shows == {}
    big = _state(1500)                           # incompressible notes force several 45k chunks
    for show in big.shows.values():
        show.note = os.urandom(40).hex()
    store.save(big)
    ws = ss.sheets["_tracker_state"]
    assert ws.hidden and len(ws.col_values(1)) > 2
    loaded = SheetStore(ss).load()
    assert len(loaded.shows) == 1500 and loaded.rev == 1
    small = _state(1)
    small.rev = loaded.rev
    s2 = SheetStore(ss)
    s2.load()
    s2.save(small)                               # shrinking leaves no stale chunks behind
    assert len(SheetStore(ss).load().shows) == 1


def test_sheet_store_detects_concurrent_writer():
    ss = FakeSpreadsheet()
    SheetStore(ss).save(_state(1))
    a, b = SheetStore(ss), SheetStore(ss)
    sa, sb = a.load(), b.load()
    sa.sent["only-a"] = "1"
    a.save(sa)
    sb.sent["only-b"] = "1"
    b.save(sb)
    assert {"only-a", "only-b"} <= set(SheetStore(ss).load().sent)
