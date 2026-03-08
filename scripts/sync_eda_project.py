#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import sys
import time
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
) -> tuple[int, bytes]:
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return int(e.code), body


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _cookie(cj: CookieJar, name: str) -> str:
    return next((c.value for c in cj if c.name == name), "")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Trigger an EDA project sync/import and wait for completion.")
    p.add_argument("--base-url", default=os.environ.get("EDA_BASE_URL", "").rstrip("/"), required=False)
    p.add_argument("--username", default=os.environ.get("EDA_USERNAME", "admin"))
    p.add_argument("--password", default=os.environ.get("EDA_PASSWORD", ""))
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--wait", type=int, default=180)
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
    status, body = _request(
        opener, method="GET", url=login_url, headers={"Accept": "application/json,text/*"}, timeout_s=args.timeout
    )
    if status < 200 or status >= 400:
        sys.stderr.write(f"GET login failed: HTTP {status}: {body[:200]!r}\n")
        return 2

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
    headers = {"Accept": "application/json", "X-CSRFToken": csrftoken, "Origin": args.base_url, "Referer": f"{args.base_url}/"}

    # Try common sync endpoints (EDA has evolved across versions).
    sync_paths = [
        f"/api/eda/v1/projects/{args.project_id}/sync/",
        f"/api/eda/v1/projects/{args.project_id}/update/",
        f"/api/eda/v1/projects/{args.project_id}/refresh/",
    ]
    triggered: str | None = None
    for path in sync_paths:
        status, body = _request(
            opener,
            method="POST",
            url=f"{args.base_url}{path}",
            headers=headers,
            timeout_s=args.timeout,
        )
        if status in (200, 201, 202, 204):
            triggered = path
            break
        if status != 404:
            sys.stderr.write(f"POST {path} -> HTTP {status}: {body[:200]!r}\n")

    if not triggered:
        sys.stderr.write("Could not find a supported project sync endpoint (all 404).\n")
        return 3

    # Wait for import_state to settle.
    deadline = time.time() + max(0, args.wait)
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        status, body = _request(
            opener,
            method="GET",
            url=f"{args.base_url}/api/eda/v1/projects/{args.project_id}/",
            headers=headers,
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            sys.stderr.write(f"GET project failed: HTTP {status}: {body[:200]!r}\n")
            time.sleep(2)
            continue
        last = _parse_json(body)
        state = (last or {}).get("import_state")
        if state in ("completed", "successful"):
            break
        if state in ("failed", "error"):
            break
        time.sleep(2)

    print(json.dumps({"triggered": triggered, "project": last}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

