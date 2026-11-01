"""A stand-in for ``requests.Session`` that routes each request to a handler.

Shared by the LINE OA tests; nothing here touches the network.
"""

from __future__ import annotations

import json

import requests


class FakeResponse:
    def __init__(self, status=200, body=None, *, url="", headers=None, lines=None, content=None):
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self.encoding = None
        self._lines = lines or []
        if content is not None:
            self.content = content
        elif body is None:
            self.content = b""
        else:
            self.content = json.dumps(body).encode()
        self.closed = False

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content.decode())

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            yield self.content[start:start + size]

    def close(self):
        self.closed = True


class FakeSession:
    """Routes requests to ``handler(call)`` and records every call."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.cookies = requests.cookies.RequestsCookieJar()

    def request(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        return self.handler(call)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        pass
