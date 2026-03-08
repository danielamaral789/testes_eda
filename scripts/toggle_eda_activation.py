#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


def _request(opener, method, url, headers, data=None, timeout_s=30.0):
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return int(e.code), body


def _cookie(cj: CookieJar, name: str) -> str:
    return next((c.value for c in cj if c.name == name), "")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Disable then enable an EDA activation to force a restart.")
    p.add_argument("--base-url", default=os.environ.get("EDA_BASE_URL", "").rstrip("/"))
    p.add_argument("--username", default=os.environ.get("EDA_USERNAME", "admin"))
    p.add_argument("--password", default=os.environ.get("EDA_PASSWORD", ""))
    p.add_argument("--activation-id", type=int, required=True)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--sleep", type=float, default=2.0)
    p.add_argument("--insecure", action="store_true")
    args = p.parse_args(argv)

    if not args.base_url:
        p.error("Missing --base-url (or env EDA_BASE_URL).")

    password = args.password or getpass.getpass(f"Password for {args.username}: ")

    cj = CookieJar()
    https_context = None
    if args.insecure and args.base_url.lower().startswith("https://"):
        https_context = ssl.create_default_context()
        https_context.check_hostname = False
        https_context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=https_context),
    )

    login_url = f"{args.base_url}/api/gateway/v1/login/"
    _request(opener, "GET", login_url, {"Accept": "application/json,text/*"}, timeout_s=args.timeout)
    csrftoken = _cookie(cj, "csrftoken")
    form = urllib.parse.urlencode({"username": args.username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        url=login_url,
        data=form,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Csrftoken": csrftoken,
            "Origin": args.base_url,
            "Referer": login_url,
            "Accept": "application/json,text/*",
        },
    )
    with opener.open(req, timeout=args.timeout) as resp:
        resp.read()
    csrftoken = _cookie(cj, "csrftoken") or csrftoken
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": args.base_url,
        "Referer": f"{args.base_url}/",
    }

    url = f"{args.base_url}/api/eda/v1/activations/{args.activation_id}/"
    for desired in (False, True):
        status, body = _request(
            opener,
            "PATCH",
            url,
            headers,
            data=json.dumps({"is_enabled": desired}).encode("utf-8"),
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise SystemExit(f"PATCH is_enabled={desired} failed: HTTP {status}: {body[:200]!r}")
        time.sleep(max(0.0, args.sleep))

    status, body = _request(opener, "GET", url, headers, timeout_s=args.timeout)
    print(body.decode("utf-8", errors="replace")[:2000])
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))

