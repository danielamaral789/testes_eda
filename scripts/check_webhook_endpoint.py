#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse_header_values(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in values:
        if ":" not in raw:
            raise ValueError(f'Invalid header (expected "Key: Value"): {raw!r}')
        key, value = raw.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def _request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout_s: float,
    insecure: bool,
) -> tuple[int, dict[str, str], bytes, str | None]:
    req = urllib.request.Request(url=url, data=body, method=method.upper())
    for key, value in headers.items():
        req.add_header(key, value)

    context = None
    if insecure and url.lower().startswith("https://"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=context) as resp:
            status = int(getattr(resp, "status", 200))
            resp_headers = {k: v for (k, v) in resp.headers.items()}
            resp_body = resp.read()
            return status, resp_headers, resp_body, None
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        resp_headers = {k: v for (k, v) in (e.headers.items() if e.headers else [])}
        resp_body = e.read() if hasattr(e, "read") else b""
        return status, resp_headers, resp_body, f"HTTPError {status}"
    except Exception as e:
        return 0, {}, b"", f"{type(e).__name__}: {e}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Send a single event to the webhook and print a minimal diagnosis (without putting token on the CLI)."
    )
    parser.add_argument("--url", default=os.environ.get("EDA_WEBHOOK_URL", ""), help="Webhook URL.")
    parser.add_argument("--method", default="POST", choices=["POST", "PUT", "PATCH"], help="HTTP method.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='Extra header, repeatable. Format: "Key: Value".',
    )
    parser.add_argument(
        "--auth-header",
        default=os.environ.get("EDA_WEBHOOK_AUTH_HEADER", "Authorization"),
        help="Auth header name (default: Authorization).",
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get("EDA_WEBHOOK_TOKEN_FILE", ""),
        help="Read token from a file (recommended).",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate validation.")
    args = parser.parse_args(argv)

    if not args.url:
        parser.error("Missing --url (or env EDA_WEBHOOK_URL).")

    headers = _parse_header_values(args.header)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")

    token = ""
    if args.token_file:
        with open(args.token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
    else:
        token = getpass.getpass(f"Token for {args.auth_header}: ").strip()

    if token:
        headers[args.auth_header] = token

    payload = {
        "id": str(uuid.uuid4()),
        "sent_at": _utc_now_iso(),
        "source": "testes_eda_check",
        "type": "check",
        "payload": {"message": "check"},
    }

    status, resp_headers, resp_body, error = _request(
        url=args.url,
        method=args.method,
        headers=headers,
        body=_json_dumps(payload).encode("utf-8"),
        timeout_s=args.timeout,
        insecure=args.insecure,
    )

    preview = resp_body[:500].decode("utf-8", errors="replace")
    print(
        json.dumps(
            {
                "status": status,
                "error": error,
                "content_type": resp_headers.get("Content-Type", ""),
                "body_preview": preview,
                "tips": (
                    "If 403: check token/header key. If 503: endpoint overloaded or upstream unavailable."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))

