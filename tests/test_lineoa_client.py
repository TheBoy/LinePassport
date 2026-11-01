"""The OA Manager client, its event stream and message normalising, offline."""

from __future__ import annotations

import threading
import time

import pytest
import requests
from lineoa_fakes import FakeResponse, FakeSession

from okline.lineoa.channels import (
    AUTO_RESPONSE_BIZ_ID,
    LineOAChannels,
    OAChannelWorker,
    is_user_chat,
    normalize_event,
)
from okline.lineoa.client import (
    OAManagerClient,
    OAManagerError,
    export_cookies,
    import_cookies,
    new_send_id,
)
from okline.lineoa.stream import OAManagerStream, parse_sse

BOT = "U" + "a" * 32
CHAT = "U" + "b" * 32


def _client(handler) -> tuple[OAManagerClient, FakeSession]:
    session = FakeSession(handler)
    return OAManagerClient(session=session), session


def test_cookies_round_trip_and_keep_only_line():
    jar = requests.cookies.RequestsCookieJar()
    jar.set("ses", "abc", domain="chat.line.biz", path="/", expires=int(time.time()) + 3600)
    jar.set("old", "x", domain="chat.line.biz", path="/", expires=int(time.time()) - 10)
    jar.set("other", "y", domain="example.com", path="/")
    jar.set("lg", "z", domain=".access.line.me", path="/")
    saved = export_cookies(jar)
    assert {item["name"] for item in saved} == {"ses", "lg"}
    again = requests.cookies.RequestsCookieJar()
    import_cookies(again, saved)
    assert again.get("ses", domain="chat.line.biz") == "abc"


def test_send_id_matches_oa_manager():
    send_id = new_send_id(CHAT)
    chat, millis, number = send_id.split("_")
    assert chat == CHAT and len(millis) == 13 and len(number) == 8 and number.isdigit()


def test_writes_carry_the_csrf_header_and_body():
    def handler(call):
        if call["url"].endswith("/api/v1/csrfToken"):
            return FakeResponse(body={"headerName": "X-XSRF-TOKEN", "token": "t1"})
        return FakeResponse(200)

    client, session = _client(handler)
    send_id = client.send_text(BOT, CHAT, "สวัสดี")
    send = session.calls[-1]
    assert send["method"] == "POST"
    assert send["url"].endswith(f"/api/v1/bots/{BOT}/chats/{CHAT}/messages/send")
    assert send["headers"]["X-XSRF-TOKEN"] == "t1"
    assert send["json"] == {"type": "text", "text": "สวัสดี", "id": "", "sendId": send_id}
    client.mark_read(BOT, CHAT, "m1", 1700000000000)
    assert session.calls[-1]["json"] == {
        "lastMessage": {"messageId": "m1", "timestamp": 1700000000000}
    }
    # The token is fetched once and reused.
    assert sum(call["url"].endswith("/csrfToken") for call in session.calls) == 1


def test_a_rejected_csrf_token_is_fetched_again_once():
    tokens = iter(["old", "new"])
    seen = []

    def handler(call):
        if call["url"].endswith("/api/v1/csrfToken"):
            return FakeResponse(body={"token": next(tokens)})
        seen.append(call["headers"]["X-XSRF-TOKEN"])
        return FakeResponse(403 if len(seen) == 1 else 200)

    client, _session = _client(handler)
    client.send_text(BOT, CHAT, "hi")
    assert seen == ["old", "new"]


@pytest.mark.parametrize("status", [401, 302])
def test_an_ended_session_is_reported_as_unauthorized(status):
    client, _session = _client(lambda call: FakeResponse(status, url=call["url"]))
    with pytest.raises(OAManagerError) as caught:
        client.me()
    assert caught.value.unauthorized


def test_network_errors_name_no_url():
    def handler(call):
        raise requests.ConnectionError("https://chat.line.biz/?token=secret")

    client, _session = _client(handler)
    with pytest.raises(OAManagerError) as caught:
        client.bots()
    assert caught.value.network and "secret" not in caught.value.message


def test_content_checks_the_id_and_size():
    client, _session = _client(lambda call: FakeResponse(content=b"x" * 10, headers={"Content-Type": "image/jpeg"}))
    data, kind = client.content(BOT, "hash1")
    assert data == b"x" * 10 and kind == "image/jpeg"
    with pytest.raises(OAManagerError):
        client.content(BOT, "../etc")
    with pytest.raises(OAManagerError) as caught:
        client.content(BOT, "hash1", max_bytes=5)
    assert caught.value.status == 413


def test_parse_sse_handles_names_ids_comments_and_multiline_data():
    lines = [": hello", "event: init", 'data: {"connectionId":"c1"}', "id: 1", "",
             "event: ping", "data: ping", "", "event: chat", "data: {\"a\":", "data: 1}", "id: 2", ""]
    events = list(parse_sse(lines))
    assert [(e.type, e.id) for e in events] == [("init", "1"), ("ping", "1"), ("chat", "2")]
    assert events[2].data == '{"a":\n1}'


def test_stream_reports_idle_and_tracks_the_last_event_id():
    states = []
    lines = ["event: init", 'data: {"connectionId":"c1"}', "id: e1", "",
             "event: ping", "data: ping", "id: e9", "",
             "event: chat", 'data: {"event":"chat","subEvent":"message"}', "id: e2", ""]

    def handler(call):
        url = call["url"]
        if url.endswith("/streamingApiToken"):
            return FakeResponse(body={"streamingApiToken": "tok", "streamingApiVersion": "v2",
                                      "streamingApiBaseUrl": "https://stream.test",
                                      "expiredAt": int(time.time() * 1000) + 3_600_000})
        if url.endswith("/csrfToken"):
            return FakeResponse(body={"token": "t"})
        if url.endswith("/streaming/state"):
            states.append(call["json"])
            return FakeResponse(200)
        if url == "https://stream.test/api/v2/sse":
            assert call["params"]["lastEventId"] == "e0"
            assert call["params"]["clientType"] == "PC"
            return FakeResponse(lines=lines)
        raise AssertionError(url)

    client, session = _client(handler)
    client._stream_session = session
    stream = OAManagerStream(client, BOT, last_event_id="e0")
    events = list(stream.events(threading.Event()))
    assert [event["type"] for event in events] == ["init", "chat"]
    assert states == [{"connectionId": "c1", "idle": True}]
    assert stream.last_event_id == "e2"


def test_stream_says_when_the_session_ended():
    def handler(call):
        if call["url"].endswith("/streamingApiToken"):
            return FakeResponse(body={"streamingApiToken": "tok"})
        if call["url"].endswith("/csrfToken"):
            return FakeResponse(body={"token": "t"})
        return FakeResponse(401)

    client, session = _client(handler)
    client._stream_session = session
    with pytest.raises(OAManagerError) as caught:
        list(OAManagerStream(client, BOT).events(threading.Event()))
    assert caught.value.unauthorized


def _event(kind="message", **message):
    event = {"type": kind, "source": {"chatId": CHAT, "userId": CHAT},
             "timestamp": 1700000000000, "message": {"id": "m1", "type": "text", "text": "ทดสอบ", **message}}
    return event


def test_normalize_event_kinds():
    incoming = normalize_event(_event(), names={CHAT: "Toon"})
    assert incoming["externalId"] == "m1" and incoming["name"] == "Toon"
    assert incoming["outgoing"] is False and incoming["contentType"] == 0
    assert incoming["text"] == "ทดสอบ" and incoming["meta"]["messageId"] == "m1"

    sent = normalize_event({**_event("messageSent"), "sendId": f"{CHAT}_1_00000001", "bizId": "biz-1"})
    assert sent["outgoing"] and sent["externalId"] == f"{CHAT}_1_00000001"
    assert sent["meta"]["bizId"] == "biz-1" and not sent["autoResponse"]

    auto = normalize_event({**_event("messageSent"), "bizId": AUTO_RESPONSE_BIZ_ID})
    assert auto["autoResponse"] and auto["externalId"] == "m1"

    image = normalize_event(_event(type="image", text="", contentProvider={"type": "line", "contentHash": "h1"}))
    assert image["contentType"] == 1 and image["meta"]["contentHash"] == "h1" and image["text"] == ""

    file = normalize_event(_event(type="file", text="", fileName="a.pdf", fileSize=12, contentHash="h2"))
    assert file["contentType"] == 14 and file["meta"]["fileName"] == "a.pdf"

    sticker = normalize_event(_event(type="sticker", text="", stickerId="52002734", packageId="11537"))
    assert sticker["contentType"] == 7 and sticker["meta"]["stickerId"] == "52002734"

    place = normalize_event(_event(type="location", text="", title="ร้าน", latitude=13.7, longitude=100.5))
    assert place["contentType"] == 15 and place["text"] == "ร้าน"
    assert place["meta"]["location"]["latitude"] == 13.7

    flex = normalize_event(_event(type="flex", text=""))
    assert flex["contentType"] == 0 and flex["text"] == "[flex]"

    assert normalize_event({**_event(), "source": {"chatId": "C" + "c" * 32}}) is None
    assert normalize_event(_event(type="unsent")) is None
    assert normalize_event({**_event(), "type": "follow"}) is None
    assert is_user_chat(CHAT) and not is_user_chat("u" + "b" * 32)


class FakeHost:
    def __init__(self):
        self.received: list[dict] = []
        self.backfilled: list[dict] = []
        self.auth_failed = threading.Event()
        self.saved_cookies: list = []
        self.cursor = ""
        self.problems: list[str] = []

    def oa_bot_id(self, account_id):
        return BOT

    def oa_cookies(self, account_id):
        return [{"name": "ses", "value": "1", "domain": "chat.line.biz", "path": "/"}]

    def oa_save_cookies(self, account_id, cookies):
        self.saved_cookies.append(cookies)

    def oa_proxy(self, account_id):
        return None

    def oa_receive(self, account_id, message):
        self.received.append(message)

    def oa_backfill(self, account_id, messages):
        self.backfilled.extend(messages)

    def oa_auth_failed(self, account_id):
        self.auth_failed.set()

    def oa_problem(self, account_id, problem):
        self.problems.append(problem)

    def oa_cursor(self, account_id):
        return self.cursor

    def oa_save_cursor(self, account_id, cursor):
        self.cursor = cursor


class FakeOAClient:
    def __init__(self, cookies=None, proxy=None):
        self.events: list[dict] = []
        self.refresh_ok = False
        self.base_url = "https://chat.line.biz"

    def chats(self, bot_id, *, next_cursor=None, limit=25):
        return [{"chatId": CHAT, "chatType": "USER", "profile": {"name": "Toon"}},
                {"chatId": "C" + "c" * 32, "chatType": "GROUP", "profile": {}}], ""

    def messages(self, bot_id, chat_id, *, backward=None):
        return [_event("messageSent", id="m-new"), _event(id="m-old")], ""  # newest first

    def chat(self, bot_id, chat_id):
        return {"profile": {"name": "Toon"}}

    def refresh_session(self):
        return self.refresh_ok

    def cookies(self):
        return []

    def close(self):
        pass


def test_worker_syncs_then_streams_and_gives_up_when_signed_out(monkeypatch):
    host = FakeHost()
    channels = LineOAChannels(host, client_factory=FakeOAClient)
    payload = {**_event(), "message": {"id": "m2", "type": "text", "text": "ใหม่"}}

    class OneShotStream:
        calls = 0

        def __init__(self, client, bot_id, *, last_event_id=""):
            self.last_event_id = last_event_id

        def events(self, stop):
            OneShotStream.calls += 1
            if OneShotStream.calls > 1:
                raise OAManagerError("ended", status=401)
            self.last_event_id = "e5"
            yield {"type": "chat", "data": {"event": "chat", "subEvent": "message", "payload": payload}}

        def close(self):
            pass

    monkeypatch.setattr("okline.lineoa.channels.OAManagerStream", OneShotStream)
    channels.start("acc-oa")
    assert host.auth_failed.wait(5)
    assert [m["externalId"] for m in host.backfilled] == ["m-old", "m-new"]
    assert host.received[0]["text"] == "ใหม่" and host.received[0]["name"] == "Toon"
    assert host.cursor == "e5"
    channels.close()


def test_worker_reports_an_oa_with_chat_turned_off(monkeypatch):
    host = FakeHost()
    attempts = []

    class ChatOffClient(FakeOAClient):
        def chats(self, bot_id, *, next_cursor=None, limit=25):
            attempts.append(1)
            if len(attempts) == 1:  # chat mode off in OA Manager, then turned on
                raise OAManagerError("LINE OA Manager answered with HTTP 403.", status=403)
            return super().chats(bot_id, next_cursor=next_cursor, limit=limit)

    class QuietStream:
        def __init__(self, client, bot_id, *, last_event_id=""):
            self.last_event_id = last_event_id

        def events(self, stop):
            stop.wait(5)
            return
            yield  # pragma: no cover

        def close(self):
            pass

    monkeypatch.setattr("okline.lineoa.channels.CHAT_OFF_RETRY_SECONDS", 0.01)
    monkeypatch.setattr("okline.lineoa.channels.OAManagerStream", QuietStream)
    channels = LineOAChannels(host, client_factory=ChatOffClient)
    channels.start("acc-oa")
    deadline = time.time() + 5
    while host.problems != ["chat_off", ""] and time.time() < deadline:
        time.sleep(0.02)
    assert host.problems == ["chat_off", ""]
    channels.close()


def test_worker_signs_in_again_quietly_when_line_allows(monkeypatch):
    host = FakeHost()
    host.cursor = "e1"  # not the first connect: no sync
    made: list[FakeOAClient] = []

    def factory(cookies=None, proxy=None):
        client = FakeOAClient(cookies, proxy)
        client.refresh_ok = True
        made.append(client)
        return client

    channels = LineOAChannels(host, client_factory=factory)
    attempts = []

    class FlakyStream:
        def __init__(self, client, bot_id, *, last_event_id=""):
            self.last_event_id = last_event_id

        def events(self, stop):
            attempts.append(1)
            if len(attempts) == 1:
                raise OAManagerError("ended", status=401)
            stop.wait(5)
            return
            yield  # pragma: no cover

        def close(self):
            pass

    monkeypatch.setattr("okline.lineoa.channels.OAManagerStream", FlakyStream)
    channels.start("acc-oa")
    deadline = time.time() + 5
    while len(attempts) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert len(attempts) >= 2 and host.saved_cookies and not host.auth_failed.is_set()
    assert not host.backfilled
    channels.close()
    assert isinstance(channels.workers, dict) and not channels.workers
    assert OAChannelWorker  # exported for the webapp's tests
