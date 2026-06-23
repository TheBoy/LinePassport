"""OkLine command-line interface.

    python -m okline endpoints                 # list every endpoint key
    python -m okline call <Endpoint> [argsJSON] --token <T>
    python -m okline profile --token <T>
    python -m okline qr-login [--save tokens.json]
    python -m okline version

``call`` lets you hit *any* endpoint and paste its full response::

    python -m okline call Talk.TalkService.getProfile "[2]" --token "$TOKEN" --raw
    python -m okline call Talk.TalkService.sendMessage \
        '[0,{"to":"u...","text":"hi","contentType":0,"contentMetadata":{}}]' --token "$TOKEN"

Tokens come from ``--token`` / ``--refresh`` or the ``LINE_ACCESS_TOKEN`` /
``LINE_REFRESH_TOKEN`` environment variables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, List, Optional

from . import __version__
from .client import OkLine, _safe_print
from .endpoints import THRIFT_ENDPOINTS
from .qrterm import print_qr


def _load_tokens_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not read tokens file {path!r}: {exc}", file=sys.stderr)
        return {}


def _make_client(args: argparse.Namespace, *, record: bool = True) -> OkLine:
    access = getattr(args, "token", None) or os.environ.get("LINE_ACCESS_TOKEN")
    refresh = getattr(args, "refresh", None) or os.environ.get("LINE_REFRESH_TOKEN")
    tokens_file = getattr(args, "tokens_file", None)
    if tokens_file:
        data = _load_tokens_file(tokens_file)
        access = access or data.get("accessToken") or data.get("access_token")
        refresh = refresh or data.get("refreshToken") or data.get("refresh_token")
    return OkLine(
        access_token=access,
        refresh_token=refresh,
        record=record,
        redact=not getattr(args, "show_secrets", False),
    )


def _print_json(value: Any) -> None:
    _safe_print(json.dumps(value, ensure_ascii=False, indent=2))


# -- commands ---------------------------------------------------------------
def cmd_endpoints(args: argparse.Namespace) -> int:
    keys = sorted(THRIFT_ENDPOINTS)
    if args.grep:
        keys = [k for k in keys if args.grep.lower() in k.lower()]
    for k in keys:
        print(f"{k:55}  /api/{THRIFT_ENDPOINTS[k]}")
    print(f"\n{len(keys)} endpoint(s)", file=sys.stderr)
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    try:
        parsed: List[Any] = json.loads(args.args) if args.args else []
    except ValueError as exc:
        print(f"error: --args must be a JSON array: {exc}", file=sys.stderr)
        return 2
    if not isinstance(parsed, list):
        print("error: args JSON must be an array of positional thrift args",
              file=sys.stderr)
        return 2
    if args.endpoint not in THRIFT_ENDPOINTS:
        print(f"error: unknown endpoint {args.endpoint!r} "
              f"(try `python -m okline endpoints`)", file=sys.stderr)
        return 2

    api = _make_client(args)
    try:
        require_auth = not args.no_auth
        result = api.transport.call(args.endpoint, parsed, require_auth=require_auth)
        if args.raw and api.last:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        else:
            _print_json(result)
        return 0
    except Exception as exc:  # noqa: BLE001 - surface any failure cleanly
        print(f"error: {exc}", file=sys.stderr)
        if args.raw and api.last:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        return 1
    finally:
        api.close()


def cmd_profile(args: argparse.Namespace) -> int:
    api = _make_client(args)
    try:
        prof = api.get_profile()
        _print_json(prof)
        if args.raw:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def cmd_qr_login(args: argparse.Namespace) -> int:
    api = OkLine(record=True, redact=not args.show_secrets)

    def show_qr(url: str) -> None:
        print("\nScan with LINE app (Add friends > QR code):\n")
        print_qr(url, border=2, invert=args.invert)
        print("\nor open:", url, "\n")

    def show_pin(pin: str) -> None:
        print(f"\n>>> Enter / confirm this PIN on your phone: {pin}\n")

    try:
        result = api.auth.qr_login(on_qr=show_qr, on_pin=show_pin,
                                   wait_seconds=args.wait)
        if not result.access_token:
            print(f"login did not complete: type={result.type} "
                  f"{result.display_message or ''}", file=sys.stderr)
            return 1
        print("login OK.")
        tokens = {
            "accessToken": result.access_token,
            "refreshToken": result.refresh_token,
            "certificate": result.certificate,
            "mid": api.tokens.mid,
        }
        try:
            prof = api.get_profile()
            tokens["mid"] = prof.get("mid") if isinstance(prof, dict) else None
            print("profile:")
            _print_json(prof)
        except Exception:  # noqa: BLE001
            pass
        if args.save:
            with open(args.save, "w", encoding="utf-8") as fh:
                json.dump(tokens, fh, ensure_ascii=False, indent=2)
            print(f"tokens saved to {args.save}")
        else:
            print("\ntokens (keep these secret):")
            _print_json(tokens)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def cmd_selftest(args: argparse.Namespace) -> int:
    from .selftest import print_results, run_selftest
    api = _make_client(args)
    if not api.tokens.access_token:
        print("error: selftest needs a token (--token or LINE_ACCESS_TOKEN)",
              file=sys.stderr)
        return 2
    try:
        results = run_selftest(api, verbose=args.verbose)
        fails = print_results(results)
        if args.save:
            api.save_log(args.save, fmt="text", redact=not args.show_secrets)
            print(f"\nfull transcript saved to {args.save}")
        return 1 if fails else 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def cmd_send(args: argparse.Namespace) -> int:
    api = _make_client(args)
    if not api.tokens.access_token:
        print("error: send needs a token (--token / --tokens-file)", file=sys.stderr)
        return 2
    try:
        if args.image:
            res = api.send_image(args.to, args.image)
        elif args.file:
            res = api.send_file(args.to, args.file)
        else:
            if not args.text:
                print("error: provide TEXT, or --image/--file", file=sys.stderr)
                return 2
            res = api.send_text(args.to, args.text)
        _print_json(res)
        if args.raw:
            _safe_print(api.last.pretty(redact=not args.show_secrets))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


def cmd_version(args: argparse.Namespace) -> int:
    print(f"OkLine {__version__} (emulates LINE CHROMEOS 3.7.2)")
    return 0


# -- parser -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="okline", description="OkLine — LINE Chrome API client")

    # Shared auth options — attached to every subcommand so they work *after*
    # the subcommand name (e.g. `okline selftest --tokens-file tokens.json`).
    auth = argparse.ArgumentParser(add_help=False)
    auth.add_argument("--token", help="X-Line-Access token (or env LINE_ACCESS_TOKEN)")
    auth.add_argument("--refresh", help="refresh token (or env LINE_REFRESH_TOKEN)")
    auth.add_argument("--tokens-file", help="JSON file with accessToken/refreshToken "
                      "(as written by `qr-login --save`)")
    auth.add_argument("--show-secrets", action="store_true",
                      help="do not redact tokens/secrets in transcripts")

    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("endpoints", parents=[auth], help="list all endpoint keys")
    e.add_argument("grep", nargs="?", help="filter substring")
    e.set_defaults(func=cmd_endpoints)

    c = sub.add_parser("call", parents=[auth],
                       help="call any endpoint and print its response")
    c.add_argument("endpoint", help="Namespace.Service.method (see `endpoints`)")
    c.add_argument("args", nargs="?", default="[]",
                   help='positional thrift args as a JSON array, e.g. "[2]"')
    c.add_argument("--raw", action="store_true", help="print the full HTTP transcript")
    c.add_argument("--no-auth", action="store_true",
                   help="do not require/send an access token (pre-login endpoints)")
    c.set_defaults(func=cmd_call)

    pr = sub.add_parser("profile", parents=[auth], help="fetch and print your profile")
    pr.add_argument("--raw", action="store_true")
    pr.set_defaults(func=cmd_profile)

    q = sub.add_parser("qr-login", parents=[auth],
                       help="log in by scanning a terminal QR code")
    q.add_argument("--save", help="write the issued tokens to this JSON file")
    q.add_argument("--wait", type=float, default=180.0, help="seconds to wait for scan/PIN")
    q.add_argument("--invert", action="store_true", help="invert QR colours (light terminal)")
    q.set_defaults(func=cmd_qr_login)

    sd = sub.add_parser("send", parents=[auth], help="send a text/image/file message")
    sd.add_argument("to", help="destination mid (u…/c…/r…)")
    sd.add_argument("text", nargs="?", help="message text")
    sd.add_argument("--image", help="path to an image to send")
    sd.add_argument("--file", help="path to a file to send")
    sd.add_argument("--raw", action="store_true", help="print the full transcript")
    sd.set_defaults(func=cmd_send)

    st = sub.add_parser("selftest", parents=[auth],
                        help="call every read-only endpoint and report pass/fail")
    st.add_argument("--verbose", action="store_true", help="print each check as it runs")
    st.add_argument("--save", help="save the full transcript to this file")
    st.set_defaults(func=cmd_selftest)

    v = sub.add_parser("version", parents=[auth], help="print version")
    v.set_defaults(func=cmd_version)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
