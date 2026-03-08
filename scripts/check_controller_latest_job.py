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


def _request(opener, method, url, headers, payload=None, timeout_s=30.0):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
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
    p = argparse.ArgumentParser(description="Check latest Controller job for a given job template id.")
    p.add_argument("--base-url", default=os.environ.get("EDA_BASE_URL", "").rstrip("/"))
    p.add_argument("--username", default=os.environ.get("EDA_USERNAME", "admin"))
    p.add_argument("--password", default=os.environ.get("EDA_PASSWORD", ""))
    p.add_argument("--job-template-id", type=int, required=True)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--wait", type=int, default=120, help="Wait up to N seconds for a job to appear/finish.")
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

    controller_api = f"{args.base_url}/api/controller/v2"
    deadline = time.time() + max(0, args.wait)
    latest: dict[str, Any] | None = None
    while time.time() < deadline:
        status, body = _request(
            opener,
            "GET",
            f"{controller_api}/jobs/?order_by=-id&job_template={args.job_template_id}",
            headers,
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            sys.stderr.write(f"GET jobs failed: HTTP {status}: {body[:200]!r}\n")
            time.sleep(2)
            continue
        obj = _parse_json(body)
        results = obj.get("results", []) if isinstance(obj, dict) else []
        if results:
            latest = results[0]
            if latest.get("status") in ("successful", "failed", "error", "canceled"):
                break
        time.sleep(2)

    print(json.dumps({"job": latest}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

