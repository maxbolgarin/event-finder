import pytest
import requests

from tracker.telegram import Telegram, TelegramError

TOKEN = "123456:SECRET-TOKEN"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json, timeout):
        self.calls.append((url, dict(json)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tg(*responses):
    session = Session(*responses)
    return Telegram(TOKEN, "42", session=session, sleep=lambda s: None), session


def test_send_ok_with_html_and_silence():
    t, s = tg(Resp(200, {"ok": True}))
    t.send("<b>hi</b>", silent=True)
    url, payload = s.calls[0]
    assert url.endswith(f"/bot{TOKEN}/sendMessage")
    assert payload["parse_mode"] == "HTML" and payload["disable_notification"] is True
    assert payload["link_preview_options"] == {"is_disabled": True}


def test_previews_the_ticket_page():
    t, s = tg(Resp(200, {"ok": True}))
    t.send("x", preview="https://www.livenation.nl/en/event/x-edp1")
    assert s.calls[0][1]["link_preview_options"] == {"url": "https://www.livenation.nl/en/event/x-edp1"}


def test_a_preview_telegram_rejects_is_dropped_not_the_alert():
    t, s = tg(Resp(400, {"ok": False, "description": "Bad Request: invalid link preview"}),
              Resp(200, {"ok": True}))
    t.send("<b>hi</b>", preview="https://x.nl/event/1")
    assert s.calls[1][1]["link_preview_options"] == {"is_disabled": True}
    assert s.calls[1][1]["parse_mode"] == "HTML"


def test_retries_after_rate_limit():
    t, s = tg(Resp(429, {"ok": False, "parameters": {"retry_after": 1}}), Resp(200, {"ok": True}))
    t.send("x")
    assert len(s.calls) == 2


def test_bad_html_falls_back_to_plain_text():
    t, s = tg(Resp(400, {"ok": False, "description": "can't parse entities"}), Resp(200, {"ok": True}))
    t.send('<b>Korn</b> <a href="https://x.nl">Tickets</a>')
    assert "parse_mode" not in s.calls[1][1]
    assert s.calls[1][1]["text"] == "Korn Tickets: https://x.nl"


def test_errors_never_leak_the_token():
    err = requests.exceptions.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")
    t, _ = tg(err, err, err, err)
    with pytest.raises(TelegramError) as exc:
        t.send("x")
    assert TOKEN not in str(exc.value) and "<token>" in str(exc.value)


def test_blocked_by_proxy_is_explained():
    err = requests.exceptions.ProxyError(
        f"HTTPSConnectionPool: url: /bot{TOKEN}/sendMessage (Caused by ProxyError('Unable to connect to "
        "proxy', OSError('Tunnel connection failed: 403 Forbidden')))")
    t, s = tg(err)
    with pytest.raises(TelegramError, match="network policy"):
        t.send("x")
    assert len(s.calls) == 1                     # no pointless retries


def test_client_error_is_raised():
    t, _ = tg(Resp(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}))
    with pytest.raises(TelegramError, match="blocked by the user"):
        t.send("x")
