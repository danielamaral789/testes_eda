#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any


def _request(
    opener: urllib.request.OpenerDirector,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    data: bytes | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return int(resp.status), dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return int(e.code), dict(e.headers.items()) if e.headers else {}, body


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _cookie(cj: CookieJar, name: str) -> str:
    return next((c.value for c in cj if c.name == name), "")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fetch an EDA credential type by id.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="")
    p.add_argument("--id", type=int, required=True, help="Credential type id")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--insecure", action="store_true")
    args = p.parse_args(argv)

    base = args.base_url.rstrip("/")
    password = args.password or os.environ.get("EDA_PASSWORD", "") or getpass.getpass(f"Password for {args.username}: ")

    cj = CookieJar()
    https_context = None
    if args.insecure and base.lower().startswith("https://"):
        https_context = ssl.create_default_context()
        https_context.check_hostname = False
        https_context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=https_context),
    )

    login_url = f"{base}/api/gateway/v1/login/"
    status, _, body = _request(
        opener,
        method="GET",
        url=login_url,
        headers={"Accept": "application/json,text/*"},
        timeout_s=args.timeout,
    )
    if status < 200 or status >= 400:
        raise SystemExit(f"GET login failed: HTTP {status}: {body[:200]!r}")

    csrftoken = _cookie(cj, "csrftoken")
    form = urllib.parse.urlencode({"username": args.username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        url=login_url,
        data=form,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Csrftoken": csrftoken,
            "Origin": base,
            "Referer": login_url,
            "Accept": "application/json,text/*",
        },
    )
    with opener.open(req, timeout=args.timeout) as resp:
        resp.read()

    csrftoken = _cookie(cj, "csrftoken") or csrftoken
    headers = {"Accept": "application/json", "X-CSRFToken": csrftoken, "Origin": base, "Referer": f"{base}/"}

    status, _, body = _request(
        opener,
        method="GET",
        url=f"{base}/api/eda/v1/credential-types/{args.id}/",
        headers=headers,
        timeout_s=args.timeout,
    )
    if status < 200 or status >= 400:
        raise SystemExit(f"GET credential type failed: HTTP {status}: {body[:300]!r}")
    print(json.dumps(_parse_json(body), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
