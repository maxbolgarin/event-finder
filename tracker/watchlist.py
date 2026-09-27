"""The artist watchlist and name resolution.

Findings name artists inconsistently ("Devin Towndend", "Noel Gallagher",
"Evanescence w/ Poppy", "Korn + Architects"); everything is resolved to the
exact watchlist spelling before it touches the state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import artist_aliases
from .normalize import name_key, similarity, strip_brackets, words

_FUZZY_MIN_LEN = 6
_FUZZY_THRESHOLD = 0.88
_SPLIT_RE = re.compile(
    r"\s*(?:,|\+|&|/|;|:|\bw/|\bwith\b|\band\b|\bsupport(?:ed)?(?:\s+(?:by|for|to))?\b"
    r"|\bft\.?|\bfeat\.?|\bx\b|\bvs\.?)\s*", re.I)


@dataclass
class Artist:
    name: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""          # disambiguation hint for the researcher, e.g. "US metalcore band"


class Watchlist:
    def __init__(self, artists: list[Artist]):
        self.artists: list[Artist] = []
        self._exact: dict[str, str] = {}
        self._phrases: list[tuple[str, str]] = []
        seen = set()
        for a in artists:
            k = name_key(a.name)
            if not k or k in seen:
                continue
            seen.add(k)
            self.artists.append(a)
            for variant in [a.name, *a.aliases, *artist_aliases(a.name)]:
                vk = name_key(variant)
                if not vk:
                    continue
                self._exact.setdefault(vk, a.name)
                phrase = words(variant)
                if phrase.startswith("the "):
                    phrase = phrase[4:]
                if len(vk) >= 3:
                    self._phrases.append((phrase, a.name))
        self._phrases.sort(key=lambda p: len(p[0]), reverse=True)
        self._order = {a.name: i for i, a in enumerate(self.artists)}

    @classmethod
    def from_names(cls, names: list[str]) -> "Watchlist":
        return cls([Artist(n) for n in names])

    @property
    def names(self) -> list[str]:
        return [a.name for a in self.artists]

    def get(self, name: str) -> Artist | None:
        for a in self.artists:
            if a.name == name:
                return a
        return None

    def order(self, name: str) -> int:
        return self._order.get(name, len(self._order))

    def resolve(self, text: str) -> str | None:
        """Exact / alias / typo-tolerant match of ONE artist name."""
        if not text or not text.strip():
            return None
        for candidate in (text, strip_brackets(text)):
            k = name_key(candidate)
            if k in self._exact:
                return self._exact[k]
        k = name_key(strip_brackets(text))
        if len(k) < _FUZZY_MIN_LEN:
            return None
        scored = sorted(
            ((similarity(k, key), name) for key, name in self._exact.items()
             if len(key) >= _FUZZY_MIN_LEN),
            reverse=True,
        )
        if not scored or scored[0][0] < _FUZZY_THRESHOLD:
            return None
        best_score, best = scored[0]
        rivals = [s for s, n in scored[1:] if n != best]
        if rivals and rivals[0] > best_score - 0.03:
            return None
        return best

    def find_all(self, *texts: str) -> list[str]:
        """Every watchlist artist mentioned in the texts (a line-up, a billing),
        in order of appearance so the headliner comes first."""
        found: list[str] = []
        for text in texts:
            if not text or not str(text).strip():
                continue
            text = str(text)
            hits: list[tuple[int, str]] = []
            whole = self.resolve(text)
            if whole:
                hits.append((-1, whole))
            hay = f" {words(text)} "
            for phrase, name in self._phrases:
                pos = hay.find(f" {phrase} ")
                if pos >= 0:
                    hits.append((pos, name))
            offset = 0
            for part in _SPLIT_RE.split(text):
                name = self.resolve(part)
                if name:
                    pos = hay.find(f" {words(part)} ")
                    hits.append((pos if pos >= 0 else 10_000 + offset, name))
                offset += 1
            for _pos, name in sorted(hits, key=lambda h: h[0]):
                if name not in found:
                    found.append(name)
        return found
