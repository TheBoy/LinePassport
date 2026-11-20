"""The proxy form can set up a Thai proxy from a provider's own credentials."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from urllib.parse import unquote, urlparse

import pytest
import requests

from okline import webapp
from okline.webapp import (
    OkLineWebHandler,
    OkLineWebServer,
    WebConfig,
    WebError,
    WebState,
    _proxy_check_url,
    _proxy_url_from_fields,
)


def _make_config(tmp_path) -> WebConfig:
    root = tmp_path / ".okline"
    return WebConfig(
        host="127.0.0.1",
        port=0,
        tokens_file=str(tmp_path / "tokens.json"),
        state_dir=str(root),
        accounts_file=str(root / "accounts.json"),
        accounts_dir=str(root / "accounts"),
        schedules_file=str(root / "schedules.json"),
        auth_file=str(root / "auth.json"),
    )


def _parts(url):
    parsed = urlparse(url)
    return parsed.scheme, parsed.hostname, parsed.port, unquote(parsed.username), unquote(parsed.password)


def _build(provider, **fields):
    return _proxy_url_from_fields({"proxyEnabled": True, "proxyProvider": provider, **fields})


# Each provider's gateway and username format, as its documentation gives them; {s} is
# the session id the app makes up (lowercase letters and digits).
EXPECTED = {
    "decodo": ("gate.decodo.com", 7000,
               r"user-spabc-country-th-session-(?P<s>[a-z0-9]{10})-sessionduration-1440"),
    "oxylabs": ("pr.oxylabs.io", 7777,
                r"customer-acme-cc-TH-sessid-(?P<s>[a-z0-9]{10})-sesstime-1440"),
    "brightdata": ("brd.superproxy.io", 44445,
                   r"brd-customer-hl_1a2b3c4d-zone-isp_th-country-th-session-(?P<s>[a-z0-9]{10})"),
    "litport": ("hub-eu-1.litport.net", 1337,
                r"tok123_pool-residential-kilo_country-th_sid-(?P<s>[a-z0-9]{10})_sttl-86400"),
    "shifter": ("isp.shifter.io", 443, r"th-bangkok-bangkok-as133481-Ksj25"),
}
TYPED = {
    "decodo": {"proxyUsername": "spabc"},
    "oxylabs": {"proxyUsername": "acme"},
    "brightdata": {"proxyUsername": "brd-customer-hl_1a2b3c4d-zone-isp_th"},
    "litport": {"proxyUsername": "tok123", "proxyHost": "hub-eu-1.litport.net"},
    "shifter": {"proxyUsername": "th-bangkok-bangkok-as133481-Ksj25"},
}


@pytest.mark.parametrize("provider", sorted(EXPECTED))
def test_each_provider_gets_its_gateway_and_a_thai_username(provider):
    host, port, pattern = EXPECTED[provider]
    url = _build(provider, proxyPassword="p@ss:w/rd", **TYPED[provider])
    scheme, got_host, got_port, username, password = _parts(url)
    assert (scheme, got_host, got_port, password) == ("http", host, port, "p@ss:w/rd")
    assert re.fullmatch(pattern, username), username
    # The password is percent-encoded in the URL, never pasted raw.
    assert "p@ss:w/rd" not in url


def test_every_account_gets_its_own_session_so_its_own_ip():
    sessions = set()
    for _ in range(5):
        username = _parts(_build("decodo", proxyUsername="spabc", proxyPassword="pw"))[3]
        sessions.add(re.fullmatch(EXPECTED["decodo"][2], username).group("s"))
    assert len(sessions) == 5


def test_pasted_usernames_lose_the_parts_the_app_adds():
    decodo = _parts(_build("decodo", proxyUsername="user-spabc", proxyPassword="pw"))[3]
    assert decodo.startswith("user-spabc-country-th-") and "user-user-" not in decodo
    oxylabs = _parts(_build("oxylabs", proxyUsername="customer-acme", proxyPassword="pw"))[3]
    assert oxylabs.startswith("customer-acme-cc-TH-")
    litport = _parts(_build("litport", proxyUsername="tok123_pool-residential-kilo_country-us",
                            proxyPassword="pw", proxyHost="hub-eu-1.litport.net"))[3]
    assert litport.startswith("tok123_pool-residential-kilo_country-th_")
    assert "country-us" not in litport
    # Targeting copied from a generator page is cut, so country, session and
    # duration are set once, by the app.
    decodo = _parts(_build("decodo", proxyUsername="user-spabc-country-us-sessionduration-30",
                           proxyPassword="pw"))[3]
    assert re.fullmatch(EXPECTED["decodo"][2], decodo), decodo
    oxylabs = _parts(_build("oxylabs", proxyUsername="customer-acme-cc-US-sessid-abc-sesstime-30",
                            proxyPassword="pw"))[3]
    assert re.fullmatch(EXPECTED["oxylabs"][2], oxylabs), oxylabs
    # A name that itself holds the separator stays whole.
    for pasted in ("acme_line", "acme_line_pool-residential-kilo_country-us"):
        litport = _parts(_build("litport", proxyUsername=pasted, proxyPassword="pw",
                                proxyHost="hub-eu-1.litport.net"))[3]
        assert litport.startswith("acme_line_pool-residential-kilo_country-th_sid-"), litport


def test_bright_data_pins_an_allocated_ip_when_given_one():
    url = _build("brightdata", proxyUsername="brd-customer-hl_1a2b3c4d-zone-isp_th",
                 proxyPassword="pw", proxyProviderIp="203.0.113.7")
    assert _parts(url)[3] == "brd-customer-hl_1a2b3c4d-zone-isp_th-ip-203.0.113.7"
    with pytest.raises(WebError) as bad_ip:
        _build("brightdata", proxyUsername="brd-customer-hl_1-zone-z", proxyPassword="pw",
               proxyProviderIp="not-an-ip")
    assert bad_ip.value.code == "proxy_zone_ip_invalid"
    with pytest.raises(WebError) as bad_user:
        _build("brightdata", proxyUsername="alice", proxyPassword="pw")
    assert bad_user.value.code == "proxy_zone_username_invalid"
    assert "brd-customer" in bad_user.value.message


def test_a_bright_data_username_from_the_code_generator_is_cut_back_to_the_zone():
    zone = "brd-customer-hl_1a2b3c4d-zone-isp_th"
    for pasted in (f"{zone}-country-th", f"{zone}-session-abc", f"{zone}-country-us-session-rand123"):
        username = _parts(_build("brightdata", proxyUsername=pasted, proxyPassword="pw"))[3]
        assert re.fullmatch(EXPECTED["brightdata"][2], username), username
    # A pinned IP in the name counts as the zone IP; the IP box wins when both are given.
    pinned = _parts(_build("brightdata", proxyUsername=f"{zone}-ip-203.0.113.7", proxyPassword="pw"))[3]
    assert pinned == f"{zone}-ip-203.0.113.7"
    boxed = _parts(_build("brightdata", proxyUsername=f"{zone}-ip-203.0.113.7", proxyPassword="pw",
                          proxyProviderIp="198.51.100.20"))[3]
    assert boxed == f"{zone}-ip-198.51.100.20"
    # A broken IP in the name is refused, never swapped for a rotating session.
    for broken in ("-ip-203.0.113", "-ip-203.0.113.7.", "-ip-203.0.113.7777", "-ip-abc", "-ip-",
                   "-ip--country-th"):
        with pytest.raises(WebError) as refused:
            _build("brightdata", proxyUsername=zone + broken, proxyPassword="pw")
        assert refused.value.code == "proxy_zone_ip_invalid", broken
    # Upper case, an -ip- followed by more targeting, and junk before known targeting.
    assert _parts(_build("brightdata", proxyUsername=f"{zone}-IP-203.0.113.7",
                         proxyPassword="pw"))[3] == f"{zone}-ip-203.0.113.7"
    assert _parts(_build("brightdata", proxyUsername=f"{zone}-ip-203.0.113.7-session-abc",
                         proxyPassword="pw"))[3] == f"{zone}-ip-203.0.113.7"
    upper = _parts(_build("brightdata", proxyUsername=f"{zone}-COUNTRY-us", proxyPassword="pw"))[3]
    assert re.fullmatch(EXPECTED["brightdata"][2], upper), upper
    with pytest.raises(WebError) as junk_first:
        _build("brightdata", proxyUsername=f"{zone}-foo-country-th", proxyPassword="pw")
    assert junk_first.value.code == "proxy_zone_username_invalid"
    # Only targeting after the zone is cut: a zone that happens to be called "ip" stays.
    odd_zone = "brd-customer-hl_1a2b3c4d-zone-ip"
    username = _parts(_build("brightdata", proxyUsername=f"{odd_zone}-country-us", proxyPassword="pw"))[3]
    assert username.startswith(f"{odd_zone}-country-th-session-"), username
    with pytest.raises(WebError) as junk:
        _build("brightdata", proxyUsername=f"{zone}-foo-1", proxyPassword="pw")
    assert junk.value.code == "proxy_zone_username_invalid"


def test_litport_takes_the_customer_hub_pool_and_port():
    url = _build("litport", proxyUsername="tok123", proxyPassword="pw",
                 proxyHost="hub-us-1.litport.net", proxyPort="2000",
                 proxyProviderPool="residential-lima")
    _scheme, host, port, username, _password = _parts(url)
    assert (host, port) == ("hub-us-1.litport.net", 2000)
    assert "_pool-residential-lima_country-th_" in username
    with pytest.raises(WebError):
        _build("litport", proxyUsername="tok123", proxyPassword="pw")  # no hub
    # Litport's export gives host:port; the host box takes the host only.
    with pytest.raises(WebError) as host_port:
        _build("litport", proxyUsername="tok123", proxyPassword="pw",
               proxyHost="hub-eu-1.litport.net:1337")
    assert host_port.value.code == "proxy_host_invalid"
    with pytest.raises(WebError) as bad_port:
        _build("litport", proxyUsername="tok123", proxyPassword="pw",
               proxyHost="hub-eu-1.litport.net", proxyPort="70000")
    assert bad_port.value.code == "proxy_port_invalid"
    with pytest.raises(WebError):
        _build("litport", proxyUsername="tok123", proxyPassword="pw",
               proxyHost="hub-eu-1.litport.net", proxyProviderPool="datacenter-us")


def test_nothing_typed_keeps_the_saved_proxy_and_half_typed_is_refused():
    # An edit that only picks a provider keeps the saved proxy.
    assert _build("decodo") is None
    # Leftover manual fields the provider does not use do not count as typed.
    assert _build("decodo", proxyHost="old.example.com", proxyPort="8080",
                  proxyProviderIp="203.0.113.7") is None
    with pytest.raises(WebError) as missing:
        _build("oxylabs", proxyUsername="acme")
    assert missing.value.code == "proxy_credentials_required"
    for bad in ("acme user", "acme:x", "a@b", "customer-", "user-"):
        provider = "decodo" if bad == "user-" else "oxylabs"
        with pytest.raises(WebError) as refused:
            _build(provider, proxyUsername=bad, proxyPassword="pw")
        assert refused.value.code == "proxy_username_invalid", bad
    with pytest.raises(WebError) as unknown:
        _build("acme-proxies", proxyUsername="a", proxyPassword="b")
    assert unknown.value.code == "proxy_provider_unknown"
    with pytest.raises(WebError) as spaced:
        _build("oxylabs", proxyUsername="acme user", proxyPassword="pw")
    assert spaced.value.code == "proxy_username_invalid"
    with pytest.raises(WebError) as pool:
        _build("litport", proxyUsername="tok123", proxyPassword="pw",
               proxyHost="hub-eu-1.litport.net", proxyProviderPool="datacenter-us")
    assert pool.value.code == "proxy_pool_invalid"


def test_the_tested_session_is_kept_so_the_account_keeps_that_ip():
    username = _parts(_build("decodo", proxyUsername="spabc", proxyPassword="pw",
                             proxyProviderSession="0123456789"))[3]
    assert re.fullmatch(EXPECTED["decodo"][2], username).group("s") == "0123456789"
    # Anything that is not a session the server could have made gets a new one.
    for bad in ("XYZ", "0123456789a", "012345678", "../../etc", "ABCDEF0123", 12345):
        username = _parts(_build("decodo", proxyUsername="spabc", proxyPassword="pw",
                                 proxyProviderSession=bad))[3]
        session = re.fullmatch(EXPECTED["decodo"][2], username).group("s")
        assert session != str(bad)


def test_a_legacy_proxy_url_is_checked_like_a_typed_one():
    assert _proxy_url_from_fields({"proxyUrl": "http://u:p@proxy.example.com:8080"}) == (
        "http://u:p@proxy.example.com:8080"
    )
    with pytest.raises(WebError) as too_long:
        _proxy_url_from_fields({"proxyUrl": "http://" + "a" * 2100 + ".example.com:80"})
    assert too_long.value.code == "invalid_proxy_url"
    for body in (
        {"proxyUrl": "socks4://x@10.0.0.5:6379"},
        {"proxyUrl": "socks4://x@10.0.0.5:6379", "proxyHost": "", "proxyPort": ""},
        {"proxyUrl": "http://proxy.example.com:8080/path"},
    ):
        with pytest.raises(WebError) as refused:
            _proxy_url_from_fields(body)
        assert refused.value.code == "invalid_proxy_url"


def test_manual_entry_is_unchanged_and_switching_off_wins():
    manual = {"proxyEnabled": True, "proxyProvider": "", "proxyScheme": "socks5",
              "proxyHost": "proxy.example.com", "proxyPort": "1080",
              "proxyUsername": "u", "proxyPassword": "p"}
    assert _proxy_url_from_fields(manual) == "socks5://u:p@proxy.example.com:1080"
    assert _proxy_url_from_fields({**manual, "proxyEnabled": False}) is None
    # Every field error has a code the page translates.
    for changed, code in (
        ({"proxyHost": "http://proxy.example.com"}, "proxy_host_invalid"),
        ({"proxyHost": "proxy.example.com:1080"}, "proxy_host_invalid"),
        ({"proxyHost": "a..b"}, "proxy_host_invalid"),
        ({"proxyHost": "x" * 64 + ".example.com"}, "proxy_host_invalid"),  # IDNA: label too long
        ({"proxyHost": "x" * 254}, "proxy_host_invalid"),
        ({"proxyPort": "0"}, "proxy_port_invalid"),
        ({"proxyPort": "eighty"}, "proxy_port_invalid"),
        ({"proxyScheme": "socks4"}, "proxy_scheme_invalid"),
        ({"proxyUsername": "u" * 5000}, "proxy_credentials_invalid"),
        ({"proxyPassword": "p\x00"}, "proxy_credentials_invalid"),
    ):
        with pytest.raises(WebError) as refused:
            _proxy_url_from_fields({**manual, **changed})
        assert refused.value.code == code, changed
    assert _build("decodo", proxyEnabled=False, proxyUsername="a", proxyPassword="b") is None


class _Answer:
    def __init__(self, status=200, data=None, text=None):
        self.status_code = status
        self._data = data
        self._text = text

    def json(self):
        if self._data is None:
            raise ValueError("no JSON")
        return self._data


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    web = WebState(_make_config(tmp_path))
    try:
        yield web
    finally:
        web.close()


def test_a_proxy_test_reports_the_exit_ip_and_whether_it_is_thai(state, monkeypatch):
    calls = []

    def fake_get(url, proxies=None, timeout=None):
        calls.append((url, proxies, timeout))
        return _Answer(data={"ip": "203.0.113.9", "city": "Bangkok", "country": "TH",
                             "org": "AS131445 Advanced Wireless Network"})

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    proxy = "http://u:secret@gate.decodo.com:7000"
    result = state.test_proxy(proxy)
    assert result == {"ok": True, "ip": "203.0.113.9", "country": "TH", "city": "Bangkok",
                      "org": "AS131445 Advanced Wireless Network", "thai": True}
    assert calls == [(webapp.PROXY_CHECK_URL, {"http": proxy, "https": proxy}, 20)]

    monkeypatch.setattr(webapp.requests, "get",
                        lambda *a, **k: _Answer(data={"ip": "198.51.100.4", "country": "sg"}))
    assert state.test_proxy(proxy)["thai"] is False


def test_oxylabs_and_bright_data_are_checked_with_their_own_ip_checker(state, monkeypatch):
    assert _proxy_check_url("http://u:p@pr.oxylabs.io:7777") == "https://ip.oxylabs.io/location"
    assert _proxy_check_url("http://u:p@brd.superproxy.io:44445") == "https://brdtest.com/myip.json"
    for other in ("http://u:p@gate.decodo.com:7000", "http://u:p@isp.shifter.io:443",
                  "http://u:p@notoxylabs.io:1", "socks5://proxy.example.com:1080"):
        assert _proxy_check_url(other) == webapp.PROXY_CHECK_URL
    replies = {
        # ip.oxylabs.io answers once per geo database; MaxMind is the one Oxylabs uses.
        "https://ip.oxylabs.io/location": {"ip": "203.0.113.20", "providers": {
            "dbip": {"country": "SG", "city": "Singapore"},
            "maxmind": {"country": "TH", "city": "Bangkok", "org_name": "TRUE INTERNET"}}},
        # brdtest.com may leave the IP out.
        "https://brdtest.com/myip.json": {"country": "TH", "geo": {"city": "Chiang Mai"},
                                          "asn": {"asnum": 7470, "org_name": "TRUE INTERNET"}},
    }
    monkeypatch.setattr(webapp.requests, "get", lambda url, **kwargs: _Answer(data=replies[url]))
    assert state.test_proxy("http://u:p@pr.oxylabs.io:7777") == {
        "ok": True, "ip": "203.0.113.20", "country": "TH", "city": "Bangkok",
        "org": "TRUE INTERNET", "thai": True}
    assert state.test_proxy("http://u:p@brd.superproxy.io:44445") == {
        "ok": True, "ip": "", "country": "TH", "city": "Chiang Mai",
        "org": "TRUE INTERNET", "thai": True}


def test_a_proxy_test_never_connects_through_a_proxy_url_the_form_would_refuse(state, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("no connection may be made")

    monkeypatch.setattr(webapp.requests, "get", fail)
    for url in ("socks4://x@10.0.0.5:6379", "ftp://proxy.example.com:21",
                "http://proxy.example.com:8080/x"):
        with pytest.raises(WebError) as refused:
            state.test_proxy(url)
        assert refused.value.code == "invalid_proxy_url"


@pytest.mark.parametrize("failure,code", [
    (_Answer(status=407), "proxy_auth_failed"),
    (requests.exceptions.ProxyError("Tunnel connection failed: 407 Proxy Authentication Required"),
     "proxy_auth_failed"),
    (requests.exceptions.ProxyError(
        "HTTPSConnectionPool(host='ipinfo.io', port=443): Max retries exceeded (Caused by "
        "ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 407 "
        "Proxy Authentication Required')))"), "proxy_auth_failed"),
    (requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"), "proxy_forbidden"),
    # A port or host holding "407" is not a refused password.
    (requests.exceptions.ProxyError(
        "Unable to connect to proxy: NewConnectionError(\"HTTPConnection(host='gate.decodo.com', "
        "port=34071): Failed to establish a new connection\")"), "proxy_unreachable"),
    (requests.exceptions.ProxyError("Cannot connect to proxy."), "proxy_unreachable"),
    (requests.exceptions.ConnectTimeout("timed out"), "proxy_unreachable"),
    (_Answer(status=200, data=None), "proxy_check_failed"),
    (_Answer(status=403, data={"error": "blocked"}), "proxy_check_failed"),
])
def test_a_failed_proxy_test_says_why_without_repeating_the_password(state, monkeypatch, failure, code):
    def fake_get(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    with pytest.raises(WebError) as exc:
        state.test_proxy("http://shop:topsecret@gate.decodo.com:7000")
    assert exc.value.code == code
    # A 4xx: Cloudflare replaces a 502's JSON with its own page.
    assert exc.value.status == 422
    assert "topsecret" not in exc.value.message and "topsecret" not in str(exc.value)
    assert exc.value.__cause__ is None  # the client's own error, which may hold it, is dropped
    with pytest.raises(WebError) as nothing:
        state.test_proxy(None)
    assert nothing.value.code == "proxy_missing"


def test_saving_a_provider_proxy_stores_the_built_thai_proxy(state):
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth._find_user(state.web_auth.data["users"][0]["id"])
    state.account_store.data["accounts"] = [{
        "id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner["id"], "tokenFile": "",
        "createdAt": "2026-11-20T00:00:00", "updatedAt": "2026-11-20T00:00:00",
    }]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")
    url = _proxy_url_from_fields({"proxyEnabled": True, "proxyProvider": "decodo",
                                  "proxyUsername": "spabc", "proxyPassword": "pw"})
    state.update_account("acct", "Shop", owner, proxy_enabled=True, proxy_url=url)
    stored = state.account_store.get("acct")["proxyUrl"]
    assert stored.startswith("fernet:") and "spabc" not in stored  # kept encrypted
    saved = state._proxy_url_for_account(state.account_store.get("acct"))
    assert saved == url and _parts(saved)[1] == "gate.decodo.com"


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.web_auth.setup("owner@example.com", "owner-pass")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_the_test_route_needs_an_account_manager_and_can_check_a_saved_proxy(live, monkeypatch):
    base, state = live
    seen = []
    real_get = requests.get

    checkers = {webapp.PROXY_CHECK_URL, *dict(webapp.PROXY_CHECK_URLS).values()}

    def fake_get(url, *args, **kwargs):
        if url in checkers:
            seen.append(kwargs.get("proxies"))
            return _Answer(data={"ip": "203.0.113.9", "city": "Bangkok", "country": "TH"})
        return real_get(url, *args, **kwargs)

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    fields = {"proxyEnabled": True, "proxyProvider": "oxylabs", "proxyUsername": "acme",
              "proxyPassword": "pw"}
    assert real_get.__module__  # the test client itself still uses the real requests
    signed_out = requests.post(f"{base}/api/proxy/test", json=fields, timeout=5)
    assert signed_out.status_code == 401

    owner = requests.Session()
    owner.post(f"{base}/api/auth/login",
               json={"email": "owner@example.com", "password": "owner-pass"},
               timeout=5).raise_for_status()
    tested = owner.post(f"{base}/api/proxy/test", json=fields, timeout=5)
    assert tested.status_code == 200 and tested.json()["thai"] is True
    assert urlparse(seen[-1]["https"]).hostname == "pr.oxylabs.io"
    # The answer names the session it tested; sending it back builds the same proxy,
    # so the account keeps the IP the test showed.
    session = tested.json()["session"]
    assert re.fullmatch(r"[0-9a-f]{10}", session)
    assert f"-sessid-{session}-" in unquote(urlparse(seen[-1]["https"]).username)
    again = owner.post(f"{base}/api/proxy/test",
                       json={**fields, "proxyProviderSession": session}, timeout=5)
    assert again.json()["session"] == session
    assert _proxy_url_from_fields({**fields, "proxyProviderSession": session}) == seen[-1]["https"]

    # A viewer may not make the server connect through proxies.
    state.web_auth.create_user("viewer@example.com", "viewer-pass", "viewer", [])
    viewer = requests.Session()
    viewer.post(f"{base}/api/auth/login",
                json={"email": "viewer@example.com", "password": "viewer-pass"},
                timeout=5).raise_for_status()
    assert viewer.post(f"{base}/api/proxy/test", json=fields, timeout=5).status_code == 403

    # With nothing typed, the account's saved proxy is tested.
    owner_record = state.web_auth._find_user(state.web_auth.data["users"][0]["id"])
    state.account_store.data["accounts"] = [{
        "id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner_record["id"],
        "tokenFile": "", "proxyUrl": state._encrypt_secret("http://u:p@saved.proxy.test:9000"),
        "createdAt": "2026-11-20T00:00:00", "updatedAt": "2026-11-20T00:00:00",
    }]
    state.account_store.save()
    state.web_auth.grant_account_access(owner_record["id"], "acct")
    saved = owner.post(f"{base}/api/proxy/test",
                       json={"proxyEnabled": True, "proxyProvider": "", "accountId": "acct"},
                       timeout=5)
    assert saved.status_code == 200 and "session" not in saved.json()
    assert urlparse(seen[-1]["https"]).hostname == "saved.proxy.test"
    # An account manager without access to that account cannot test its proxy.
    state.web_auth.create_user("member@example.com", "member-pass", "member", [])
    member = requests.Session()
    member.post(f"{base}/api/auth/login",
                json={"email": "member@example.com", "password": "member-pass"},
                timeout=5).raise_for_status()
    checks = len(seen)
    other = member.post(f"{base}/api/proxy/test",
                        json={"proxyEnabled": True, "proxyProvider": "", "accountId": "acct"},
                        timeout=5)
    assert other.status_code == 403 and len(seen) == checks
    nothing = owner.post(f"{base}/api/proxy/test", json={"proxyEnabled": True}, timeout=5)
    assert (nothing.status_code, nothing.json()["code"]) == (400, "proxy_missing")
