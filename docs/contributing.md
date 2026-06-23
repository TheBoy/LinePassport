# Contributing

[← docs home](./index.md)

Thanks for helping improve OkLine! This page covers dev setup, the test-suite
layout, and how to add new endpoints.

## Setup

```bash
git clone <your-fork-url>
cd OkLine
pip install -r requirements.txt
pip install pytest qrcode
# Node.js 18+ must be on PATH for X-Hmac (and the bridge tests)
node --version
```

## Running the tests

```bash
python -m pytest -q              # the whole suite (offline; no network)
python -m pytest tests/test_services_messaging.py -q   # one file
```

The suite is **offline** — it fakes the HTTP layer and the LTSM bridge, so it
needs neither network nor Node. The one exception is
`tests/test_hmac_bridge.py`, which exercises the real WASM and **skips itself**
when Node isn't available.

## Test layout

Shared fixtures live in [`tests/conftest.py`](../tests/conftest.py):

| Helper / fixture | Use |
|------------------|-----|
| `build_api(responder, *, access_token, bridge, enable_hmac, record, **kw)` | build an `OkLine` wired to a fake session |
| `enveloped(data)` | wrap data in the `{message:OK,data}` envelope |
| `route({suffix: data})` | build a responder from an endpoint→response table |
| `FakeResp`, `FakeSession`, `FakeBridge` | the fakes |
| `USER_MID`, `GROUP_MID`, `ROOM_MID`, `SAMPLE_*` | sample data |
| fixtures `make_api`, `api`, `fake_bridge`, `last_request` | convenience |

Files are split by concern: `test_transport.py`, `test_crypto.py`,
`test_enums.py`, `test_models.py`, `test_endpoints.py`, `test_recorder.py`,
`test_auth.py`, `test_qrterm.py`, `test_cli.py`, `test_hmac_bridge.py`, and
`test_services_*.py`.

### A typical service test

```python
def test_send_text_payload(make_api, last_request):
    from conftest import route, USER_MID
    api = make_api(route({"sendMessage": {"id": "1"}}))
    api.send_text(USER_MID, "hi")
    body = last_request(api)                      # the positional-args array
    assert api.transport.session.last["url"].endswith("/Talk/TalkService/sendMessage")
    req_seq, msg = body
    assert msg["text"] == "hi" and msg["contentType"] == 0
```

reqSeq values are auto-generated — assert structure, not exact numbers.

## Adding a new endpoint

1. **Register the path** in [`okline/endpoints.py`](../okline/endpoints.py)
   under `THRIFT_ENDPOINTS`, keyed `Namespace.Service.method`.
2. **Add a typed wrapper** to the right mixin in
   [`okline/services/`](../okline/services/). Follow the positional-arg
   convention — the body is a JSON array of the Thrift args in order; struct args
   are dicts with camelCase field names. Auto-generate `reqSeq` via
   `self.next_req_seq()` when the method takes one.
3. **Add a test** in the matching `test_services_*.py` asserting the URL and the
   body shape.
4. If you discover the exact argument fields, also add them to
   [`docs/ENDPOINTS.md`](./ENDPOINTS.md).

```python
# services/example.py
class ExampleMixin:
    def my_method(self, mid: str, req_seq=None):
        if req_seq is None:
            req_seq = self.next_req_seq()
        return self.transport.call("Talk.TalkService.myMethod", [req_seq, mid])
```

Then include the mixin in `services/__init__.py`'s `AllServices`.

## Style

- Type hints + concise docstrings on public methods.
- Stay **faithful to the bundle** — argument order, field names and enum values
  must match what the real client sends (cite the source when non-obvious).
- Keep secrets out of code and logs; redaction defaults must stay on.

## Releasing / publishing to PyPI

```bash
pip install build twine
python -m build                 # builds dist/*.whl and dist/*.tar.gz
twine check dist/*              # validate metadata
twine upload dist/*            # needs a PyPI account + API token
# then tag the release:
git tag v2.1.0 && git push --tags
gh release create v2.1.0 --generate-notes
```

The wheel bundles the LTSM module (`ltsm.wasm`, `ltsmSandbox.js`, the bridge) and
`py.typed`. `dist/`, `build/` and `*.egg-info/` are git-ignored.

## Scope & ethics

OkLine is for interoperability, research and use with **your own account**, in
compliance with LINE's Terms of Service. Please don't contribute features whose
primary purpose is spam, scraping others' data, or abuse.
