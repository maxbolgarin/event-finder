"""Telegram Bot API delivery (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."""

from __future__ import annotations

import os
import time

import requests

from .messages import plain

API = "https://api.telegram.org"
_MAX_LEN = 4000


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, session=None, timeout: float = 20,
                 sleep=time.sleep):
        self.token = token
        self.chat_id = chat_id
        self.session = session or requests.Session()
        self.timeout = timeout
        self._sleep = sleep

    @classmethod
    def from_env(cls) -> "Telegram | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        return cls(token, chat) if token and chat else None

    def _scrub(self, text: str) -> str:
        """requests' errors embed the URL, i.e. the bot token - never print it."""
        return str(text).replace(self.token, "<token>") if self.token else str(text)

    def send(self, html_text: str, silent: bool = False) -> None:
        if len(html_text) > _MAX_LEN:
            html_text = plain(html_text)[:_MAX_LEN - 1] + "…"
            payload_mode = None
        else:
            payload_mode = "HTML"
        payload = {"chat_id": self.chat_id, "text": html_text, "disable_web_page_preview": True,
                   "disable_notification": bool(silent)}
        if payload_mode:
            payload["parse_mode"] = payload_mode
        last = "unknown error"
        for attempt in range(4):
            try:
                resp = self.session.post(f"{API}/bot{self.token}/sendMessage", json=payload,
                                         timeout=self.timeout)
            except requests.RequestException as exc:
                msg = self._scrub(exc)
                if "403" in msg and ("Tunnel" in msg or "Proxy" in msg or "proxy" in msg):
                    raise TelegramError("api.telegram.org is blocked by this environment's network "
                                        "policy - allow it in the environment's network settings") from None
                last = f"network error: {msg[:300]}"
                self._sleep(2 ** attempt)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 200 and body.get("ok"):
                return
            desc = self._scrub(body.get("description") or resp.text[:200])
            if resp.status_code == 429:
                self._sleep(min(float((body.get("parameters") or {}).get("retry_after", 5)), 30))
                last = f"rate limited: {desc}"
                continue
            if resp.status_code == 400 and "parse_mode" in payload:
                # malformed HTML must never block an alert: resend as plain text
                payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                payload["text"] = plain(html_text)
                last = f"bad HTML: {desc}"
                continue
            if resp.status_code >= 500:
                last = f"HTTP {resp.status_code}: {desc}"
                self._sleep(2 ** attempt)
                continue
            raise TelegramError(f"HTTP {resp.status_code}: {desc}")
        raise TelegramError(last)
