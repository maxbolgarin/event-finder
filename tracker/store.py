"""Persistence of the tracker state as one JSON document.

Production keeps it in a hidden tab of the Google Sheet (gzip + base64 split
over cells, with a checksum); development and tests use a local JSON file.
Saving detects a concurrent writer (e.g. the routine and a scheduled poller)
and merges instead of overwriting, so no alert-ledger entry is ever lost.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import tempfile

from .model import State

STATE_TAB = "_tracker_state"
_CHUNK = 45_000          # Google Sheets allows 50k characters per cell
_MAGIC = "tracker-state v1"


class StateCorrupted(RuntimeError):
    pass


def encode(state: State) -> tuple[str, list[str]]:
    raw = json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":")).encode()
    blob = base64.b64encode(gzip.compress(raw, 9)).decode()
    chunks = [blob[i:i + _CHUNK] for i in range(0, len(blob), _CHUNK)] or [""]
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return f"{_MAGIC} rev={state.rev} sha={digest} chunks={len(chunks)}", chunks


def header_rev(header: str) -> int:
    for part in (header or "").split():
        if part.startswith("rev="):
            return int(part[4:])
    return 0


def decode(header: str, chunks: list[str]) -> State:
    if not header.startswith(_MAGIC):
        raise StateCorrupted(f"unexpected state header {header[:40]!r}")
    fields = dict(p.split("=", 1) for p in header.split() if "=" in p)
    n = int(fields.get("chunks", len(chunks)))
    blob = "".join(chunks[:n])
    if hashlib.sha256(blob.encode()).hexdigest()[:16] != fields.get("sha"):
        raise StateCorrupted("state checksum mismatch - refusing to use a truncated state")
    return State.from_dict(json.loads(gzip.decompress(base64.b64decode(blob))))


def merge_into(ours: State, theirs: State) -> None:
    """Fold a concurrently saved state into ours (ours wins on conflicts)."""
    for k, v in theirs.sent.items():
        ours.sent.setdefault(k, v)
    for sid, show in theirs.shows.items():
        ours.shows.setdefault(sid, show)
    queued = ours.queued_keys()
    for msg in theirs.pending:
        if not set(msg.get("keys", [])) & queued:
            ours.pending.append(msg)
    ours.pending = [m for m in ours.pending if not all(k in ours.sent for k in m.get("keys", []))]
    known_news = {n.get("key") for n in ours.news}
    ours.news.extend(n for n in theirs.news if n.get("key") not in known_news)
    for name, info in theirs.artists.items():
        mine = ours.artists.setdefault(name, {})
        for k, v in info.items():
            mine[k] = max(mine.get(k, ""), v) if k == "last_checked" else mine.get(k, v)
    ours.runs = sorted(ours.runs + [r for r in theirs.runs if r not in ours.runs],
                       key=lambda r: r.get("at", ""))[-60:]
    ours.extra = {**theirs.extra, **ours.extra}
    ours.initialized = ours.initialized or theirs.initialized
    ours.rev = max(ours.rev, theirs.rev)


class FileStore:
    def __init__(self, path: str):
        self.path = path
        self._loaded_rev: int | None = None

    def describe(self) -> str:
        return f"file {self.path}"

    def _read(self) -> State | None:
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding="utf-8") as fh:
            return State.from_dict(json.load(fh))

    def load(self) -> State:
        st = self._read() or State()
        self._loaded_rev = st.rev
        return st

    def save(self, state: State) -> None:
        current = self._read()
        if current is not None and self._loaded_rev is not None and current.rev != self._loaded_rev:
            merge_into(state, current)
        state.rev = max(state.rev, current.rev if current else 0) + 1
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self._loaded_rev = state.rev


class SheetStore:
    def __init__(self, spreadsheet, tab: str = STATE_TAB):
        self.ss = spreadsheet
        self.tab = tab
        self._loaded_rev: int | None = None

    def describe(self) -> str:
        return f"sheet tab '{self.tab}'"

    def _ws(self, create: bool = False):
        import gspread
        try:
            return self.ss.worksheet(self.tab)
        except gspread.WorksheetNotFound:
            if not create:
                return None
            ws = self.ss.add_worksheet(title=self.tab, rows=20, cols=1)
            ws.update(range_name="A1", values=[["State of the NL concert tracker - do not edit."]],
                      value_input_option="RAW")
            try:
                ws.hide()
            except Exception:          # cosmetic only
                pass
            return ws

    def _read(self, ws) -> State | None:
        cells = ws.col_values(1) if ws is not None else []
        if not cells or not cells[0].startswith(_MAGIC):
            return None
        return decode(cells[0], cells[1:])

    def load(self) -> State:
        st = self._read(self._ws()) or State()
        self._loaded_rev = st.rev
        return st

    def save(self, state: State) -> None:
        ws = self._ws(create=True)
        header = ws.acell("A1").value or ""
        current_rev = header_rev(header) if header.startswith(_MAGIC) else 0
        if header.startswith(_MAGIC) and self._loaded_rev is not None and current_rev != self._loaded_rev:
            current = self._read(ws)
            if current is not None:
                merge_into(state, current)
        state.rev = max(state.rev, current_rev) + 1
        head, chunks = encode(state)
        needed = len(chunks) + 1
        if ws.row_count < needed:
            ws.add_rows(needed - ws.row_count)
        ws.update(range_name=f"A1:A{needed}", values=[[head]] + [[c] for c in chunks],
                  value_input_option="RAW")
        if ws.row_count > needed:
            ws.batch_clear([f"A{needed + 1}:A{ws.row_count}"])
        self._loaded_rev = state.rev
