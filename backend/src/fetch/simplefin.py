"""SimpleFIN Bridge client (https://www.simplefin.org/protocol.html).

A setup token (base64 of a one-time claim URL) is exchanged once for an
access URL with HTTP basic credentials; `GET {access}/accounts` returns
accounts with their transactions. The Bridge asks clients to stay near 24
requests a day, to span at most 90 days per request, and to show its errors
to the user. In practice the Bridge warns above 45 days per request.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

# The protocol allows 90 days per request; the Bridge now warns above 45.
MAX_WINDOW_DAYS = 45
TIMEOUT_SECONDS = 60
# The Bridge (behind a CDN) refuses requests with Python's default user agent.
USER_AGENT = "kitchen-table/1.0"


class SimplefinError(RuntimeError):
    """The Bridge refused the request (bad or used token, expired access, subscription)."""


@dataclass
class Window:
    start: date
    end: date  # exclusive


def windows(start: date, end: date, days: int = MAX_WINDOW_DAYS) -> list[Window]:
    """Split [start, end) into request windows the Bridge accepts."""
    result, cursor = [], start
    while cursor < end:
        stop = min(cursor + timedelta(days=days), end)
        result.append(Window(cursor, stop))
        cursor = stop
    return result


def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def claim(setup_token: str, opener: Callable = urllib.request.urlopen) -> str:
    """Exchange a setup token for the access URL (the token works once)."""
    try:
        claim_url = base64.b64decode(setup_token.strip()).decode("utf-8")
    except ValueError as exc:
        raise SimplefinError("that is not a SimpleFIN setup token") from exc
    if not claim_url.startswith("https://"):
        raise SimplefinError("that is not a SimpleFIN setup token")
    request = urllib.request.Request(claim_url, data=b"", method="POST", headers={"Content-Length": "0", "User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8").strip()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SimplefinError("the setup token was already used or does not exist; create a new one") from exc
        raise SimplefinError(f"claiming the setup token failed (HTTP {exc.code})") from exc


@dataclass
class Response:
    window: Window
    body: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[str]:
        """Messages the Bridge wants shown to the user."""
        messages = [f"{e.get('code', 'error')}: {e.get('msg', '')}".strip() for e in self.body.get("errlist") or []]
        return messages or list(self.body.get("errors") or [])


def fetch(access_url: str, start: date, end: date, pending: bool = False,
          opener: Callable = urllib.request.urlopen) -> list[Response]:
    """Accounts and transactions posted in [start, end), one request per 45-day window."""
    parts = urllib.parse.urlsplit(access_url)
    if not parts.username:
        raise SimplefinError("the stored access URL has no credentials; connect again")
    credentials = base64.b64encode(f"{urllib.parse.unquote(parts.username)}:"
                                   f"{urllib.parse.unquote(parts.password or '')}".encode()).decode()
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    base = urllib.parse.urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
    responses = []
    for window in windows(start, end):
        query = {"version": "2", "start-date": _epoch(window.start), "end-date": _epoch(window.end)}
        if pending:
            query["pending"] = "1"
        request = urllib.request.Request(f"{base}/accounts?{urllib.parse.urlencode(query)}",
                                         headers={"Authorization": f"Basic {credentials}",
                                                  "User-Agent": USER_AGENT})
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                responses.append(Response(window, json.loads(response.read().decode("utf-8"))))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise SimplefinError("SimpleFIN refused the access URL (revoked or disabled); connect again") from exc
            if exc.code == 402:
                raise SimplefinError("the SimpleFIN Bridge subscription needs renewing") from exc
            raise SimplefinError(f"SimpleFIN request failed (HTTP {exc.code})") from exc
    return responses


def to_day(epoch: Optional[int]) -> Optional[date]:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).date() if epoch else None
