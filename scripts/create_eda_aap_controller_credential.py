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


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)


def _cookie_value(cj: CookieJar, name: str) -> str | None:
    for cookie in cj:
        if cookie.name == name:
            return cookie.value
    return None


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
            status = int(getattr(resp, "status", 200))
            resp_headers = {k: v for (k, v) in resp.headers.items()}
            body = resp.read()
            return status, resp_headers, body
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        resp_headers = {k: v for (k, v) in (e.headers.items() if e.headers else [])}
        body = e.read() if hasattr(e, "read") else b""
        return status, resp_headers, body


def _parse_json(body: bytes) -> Any:
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _get_json(
    opener: urllib.request.OpenerDirector,
    *,
    url: str,
    headers: dict[str, str],
    timeout_s: float,
) -> Any:
    status, _, body = _request(opener, method="GET", url=url, headers=headers, timeout_s=timeout_s)
    if status < 200 or status >= 400:
        raise RuntimeError(f"GET {url} failed: HTTP {status}: {body[:500]!r}")
    return _parse_json(body)


def _first_result(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict) and isinstance(obj.get("results"), list) and obj["results"]:
        first = obj["results"][0]
        return first if isinstance(first, dict) else None
    if isinstance(obj, list) and obj:
        first = obj[0]
        return first if isinstance(first, dict) else None
    return None


def _find_by_name(obj: Any, name: str) -> dict[str, Any] | None:
    results: list[Any] = []
    if isinstance(obj, dict) and isinstance(obj.get("results"), list):
        results = obj["results"]
    elif isinstance(obj, list):
        results = obj
    for item in results:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Create an EDA credential of type 'Red Hat Ansible Automation Platform' (credential_type_id=4 in this lab)."
    )
    p.add_argument("--base-url", default=os.environ.get("EDA_BASE_URL", "").rstrip("/"), required=False)
    p.add_argument("--username", default=os.environ.get("EDA_USERNAME", "admin"))
    p.add_argument("--name", default=os.environ.get("EDA_AAP_CRED_NAME", "aap-local-controller"))
    p.add_argument(
        "--host",
        default=os.environ.get("EDA_AAP_HOST", ""),
        help="AAP base URL for Controller auth; recommended: https://<gateway>/api/controller/",
    )
    p.add_argument("--verify-ssl", action="store_true", default=True)
    p.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")
    p.add_argument("--oauth-token", default=os.environ.get("EDA_AAP_OAUTH_TOKEN", ""))
    p.add_argument("--controller-username", default=os.environ.get("EDA_AAP_USERNAME", "admin"))
    p.add_argument("--controller-password", default=os.environ.get("EDA_AAP_PASSWORD", ""))
    p.add_argument("--organization", default=os.environ.get("EDA_ORGANIZATION", "Default"))
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only).")
    args = p.parse_args(argv)

    if not args.base_url:
        p.error("Missing --base-url (or env EDA_BASE_URL).")

    aap_host = args.host or f"{args.base_url}/api/controller/"

    password = os.environ.get("EDA_PASSWORD", "")
    if not password:
        password = getpass.getpass(f"Password for {args.username}: ")

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
    status, _, body = _request(
        opener, method="GET", url=login_url, headers={"Accept": "application/json,text/*"}, timeout_s=args.timeout
    )
    if status < 200 or status >= 400:
        sys.stderr.write(f"Failed to fetch login CSRF cookie: HTTP {status}\n")
        sys.stderr.write(body.decode("utf-8", errors="replace")[:500] + "\n")
        return 2
    csrftoken = _cookie_value(cj, "csrftoken") or ""
    if not csrftoken:
        sys.stderr.write("No csrftoken cookie found; cannot log in.\n")
        return 2

    form = urllib.parse.urlencode({"username": args.username, "password": password}).encode("utf-8")
    status, _, body = _request(
        opener,
        method="POST",
        url=login_url,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Csrftoken": csrftoken,
            "Origin": args.base_url,
            "Referer": login_url,
            "Accept": "application/json,text/*",
        },
        data=form,
        timeout_s=args.timeout,
    )
    if status < 200 or status >= 400:
        sys.stderr.write(f"Login failed: HTTP {status}\n")
        sys.stderr.write(body.decode("utf-8", errors="replace")[:500] + "\n")
        return 3

    csrftoken = _cookie_value(cj, "csrftoken") or csrftoken
    headers = {
        "Accept": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": args.base_url,
        "Referer": f"{args.base_url}/",
    }

    orgs = _get_json(
        opener,
        url=f"{args.base_url}/api/eda/v1/organizations/?name={urllib.parse.quote(args.organization)}",
        headers=headers,
        timeout_s=args.timeout,
    )
    org = _first_result(orgs) or _first_result(
        _get_json(opener, url=f"{args.base_url}/api/eda/v1/organizations/", headers=headers, timeout_s=args.timeout)
    )
    if not org or not isinstance(org.get("id"), int):
        raise RuntimeError(f"Could not resolve organization {args.organization!r}")
    organization_id = int(org["id"])

    # Find existing credential by name
    existing = _get_json(
        opener,
        url=f"{args.base_url}/api/eda/v1/eda-credentials/?name={urllib.parse.quote(args.name)}",
        headers=headers,
        timeout_s=args.timeout,
    )
    cred = _find_by_name(existing, args.name)
    if cred and isinstance(cred.get("id"), int):
        print(_json_dumps({"existing": {"id": int(cred['id']), "name": args.name}}))
        return 0

    inputs: dict[str, Any] = {
        "host": aap_host,
        "verify_ssl": bool(args.verify_ssl),
        "request_timeout": "10",
    }
    if args.oauth_token:
        inputs["oauth_token"] = args.oauth_token
    else:
        controller_password = args.controller_password or os.environ.get("EDA_AAP_PASSWORD", "")
        if not controller_password:
            controller_password = getpass.getpass(f"Controller password for {args.controller_username}: ")
        inputs["username"] = args.controller_username
        inputs["password"] = controller_password

    payload = {
        "name": args.name,
        "description": "Credential used by EDA to call AAP Controller (run_job_template).",
        "credential_type_id": 4,
        "organization_id": organization_id,
        "inputs": inputs,
    }
    status, _, body = _request(
        opener,
        method="POST",
        url=f"{args.base_url}/api/eda/v1/eda-credentials/",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
        timeout_s=args.timeout,
    )
    if status < 200 or status >= 400:
        raise RuntimeError(f"Failed to create EDA credential: HTTP {status}: {body[:1000]!r}")
    created = _parse_json(body)
    print(_json_dumps({"created": created}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

