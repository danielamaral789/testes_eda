#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import sys
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
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url=url, data=data, method=method.upper())
    for k, v in (headers or {}).items():
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


def _login(
    *,
    base_url: str,
    username: str,
    password: str,
    timeout_s: float,
    insecure: bool,
) -> tuple[urllib.request.OpenerDirector, CookieJar, str]:
    cj = CookieJar()
    https_context = None
    if insecure and base_url.lower().startswith("https://"):
        https_context = ssl.create_default_context()
        https_context.check_hostname = False
        https_context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=https_context),
    )

    login_url = f"{base_url}/api/gateway/v1/login/"
    status, _, body = _request(
        opener,
        method="GET",
        url=login_url,
        headers={"Accept": "application/json,text/*"},
        timeout_s=timeout_s,
    )
    if status < 200 or status >= 400:
        raise RuntimeError(f"GET login failed: HTTP {status}: {body[:200]!r}")

    csrftoken = _cookie(cj, "csrftoken")
    if not csrftoken:
        raise RuntimeError("No csrftoken cookie found.")

    form = urllib.parse.urlencode({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        url=login_url,
        data=form,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Csrftoken": csrftoken,
            "Origin": base_url,
            "Referer": login_url,
            "Accept": "application/json,text/*",
        },
    )
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        raise RuntimeError(f"Login failed: HTTP {e.code}: {body[:200]!r}") from e

    return opener, cj, _cookie(cj, "csrftoken") or csrftoken


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Print minimal schema info from EDA API via OPTIONS.")
    p.add_argument("--base-url", required=True, help="AAP base URL, e.g. https://...")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default="")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--insecure", action="store_true")
    p.add_argument(
        "--dump",
        default="",
        help="If set, dump the full OPTIONS response for this API path (e.g. /api/eda/v1/eda-credentials/).",
    )
    args = p.parse_args(argv)

    password = args.password or os.environ.get("EDA_PASSWORD", "")
    if not password:
        password = getpass.getpass(f"Password for {args.username}: ")

    opener, _, csrftoken = _login(
        base_url=args.base_url.rstrip("/"),
        username=args.username,
        password=password,
        timeout_s=args.timeout,
        insecure=args.insecure,
    )
    headers = {
        "Accept": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": args.base_url.rstrip("/"),
        "Referer": f"{args.base_url.rstrip('/')}/",
    }

    if args.dump:
        path = args.dump if args.dump.startswith("/") else "/" + args.dump
        url = f"{args.base_url.rstrip('/')}{path}"
        status, _, body = _request(opener, method="OPTIONS", url=url, headers=headers, timeout_s=args.timeout)
        if status < 200 or status >= 400:
            raise RuntimeError(f"OPTIONS {path} failed: HTTP {status}: {body[:200]!r}")
        print(body.decode("utf-8", errors="replace"))
        return 0

    def show(path: str) -> dict[str, Any]:
        url = f"{args.base_url.rstrip('/')}{path}"
        status, _, body = _request(opener, method="OPTIONS", url=url, headers=headers, timeout_s=args.timeout)
        if status < 200 or status >= 400:
            raise RuntimeError(f"OPTIONS {path} failed: HTTP {status}: {body[:200]!r}")
        obj = _parse_json(body)
        actions = (obj or {}).get("actions") if isinstance(obj, dict) else None
        post = (actions or {}).get("POST") if isinstance(actions, dict) else None
        return post if isinstance(post, dict) else {}

    def try_show(paths: list[str]) -> tuple[str, dict[str, Any]] | tuple[None, dict[str, Any]]:
        last_err: str | None = None
        for path in paths:
            try:
                return path, show(path)
            except Exception as e:
                last_err = str(e)
        return None, {"_error": last_err or "no paths tried"}

    cred_types_path, cred_types = try_show(
        [
            "/api/eda/v1/eda-credential-types/",
            "/api/eda/v1/credential-types/",
            "/api/eda/v1/credential_types/",
        ]
    )

    out = {
        "activations_POST_fields": list(show("/api/eda/v1/activations/").keys()),
        "eda_credentials_POST_fields": list(show("/api/eda/v1/eda-credentials/").keys()),
        "credential_types_endpoint": cred_types_path,
        "credential_types_POST_fields": list(cred_types.keys()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
