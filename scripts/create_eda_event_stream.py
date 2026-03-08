#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import secrets
import json
import os
import stat
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
    status, _, body = _request(
        opener,
        method="GET",
        url=url,
        headers=headers,
        timeout_s=timeout_s,
    )
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


def _best_url_from_event_stream(obj: dict[str, Any]) -> str | None:
    for key in (
        "webhook_url",
        "receiver_url",
        "endpoint_url",
        "endpoint",
        "ingress_url",
        "url",
    ):
        value = obj.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _write_secret_file(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
        f.write("\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        # Best effort only (platform/filesystem dependent).
        pass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Create an EDA Event Stream (webhook) via AAP Gateway session auth."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("EDA_BASE_URL", "").rstrip("/"),
        help="AAP/EDA base URL, e.g. https://... (or set EDA_BASE_URL).",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("EDA_USERNAME", "admin"),
        help="Username (or set EDA_USERNAME).",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("EDA_EVENT_STREAM_NAME", "lab-webhook"),
        help="Event stream name (or set EDA_EVENT_STREAM_NAME).",
    )
    parser.add_argument(
        "--description",
        default=os.environ.get("EDA_EVENT_STREAM_DESCRIPTION", "Created by testes_eda"),
        help="Event stream description.",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Create in test_mode=true (if supported).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("EDA_EVENT_STREAM_TOKEN", ""),
        help="Shared token used by Token Event Stream credential (or set EDA_EVENT_STREAM_TOKEN). If empty, a random token is generated.",
    )
    parser.add_argument(
        "--http-header-key",
        default=os.environ.get("EDA_EVENT_STREAM_TOKEN_HEADER", "Authorization"),
        help="HTTP header key used to pass the token (default: Authorization).",
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="Rotate token by creating a new credential and re-pointing the event stream (creates a new credential with a random suffix).",
    )
    parser.add_argument(
        "--write-token",
        default=os.environ.get("EDA_EVENT_STREAM_TOKEN_FILE", ""),
        help="If set, write the generated token to this file (0600 best-effort) and redact it from stdout JSON.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate validation (use only for testing).",
    )
    args = parser.parse_args(argv)

    if not args.base_url:
        parser.error("Missing --base-url (or env EDA_BASE_URL).")

    password = os.environ.get("EDA_PASSWORD")
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
    origin = args.base_url

    status, _, body = _request(
        opener,
        method="GET",
        url=login_url,
        headers={"Accept": "application/json,text/*"},
        timeout_s=args.timeout,
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
            "Origin": origin,
            "Referer": login_url,
            "Accept": "application/json,text/*",
        },
        data=form,
        timeout_s=args.timeout,
    )
    if status in (400, 401, 403):
        sys.stderr.write("Login failed (invalid credentials or forbidden).\n")
        sys.stderr.write(body.decode("utf-8", errors="replace")[:500] + "\n")
        return 3
    if status < 200 or status >= 400:
        sys.stderr.write(f"Login failed: HTTP {status}\n")
        sys.stderr.write(body.decode("utf-8", errors="replace")[:500] + "\n")
        return 3

    # EDA API calls use the same session cookies + CSRF header pattern.
    csrftoken = _cookie_value(cj, "csrftoken") or csrftoken

    common_headers = {
        "Accept": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": origin,
        "Referer": f"{args.base_url}/",
    }

    # Resolve organization_id (Default if only one).
    orgs = _get_json(
        opener,
        url=f"{args.base_url}/api/eda/v1/organizations/?name=Default",
        headers=common_headers,
        timeout_s=args.timeout,
    )
    org = _first_result(orgs) or _first_result(
        _get_json(
            opener,
            url=f"{args.base_url}/api/eda/v1/organizations/",
            headers=common_headers,
            timeout_s=args.timeout,
        )
    )
    if not org or not isinstance(org.get("id"), int):
        sys.stderr.write("Could not resolve organization_id.\n")
        print(_json_dumps({"organizations": orgs}))
        return 4
    organization_id = int(org["id"])

    # Ensure an EDA credential exists for the event stream (Token Event Stream credential type id=8).
    token_value: str | None = args.token or None
    credential_name = f"{args.name}-token"

    creds_list = _get_json(
        opener,
        url=f"{args.base_url}/api/eda/v1/eda-credentials/?name={urllib.parse.quote(credential_name)}",
        headers=common_headers,
        timeout_s=args.timeout,
    )
    cred = _find_by_name(creds_list, credential_name)
    if cred and args.rotate_token:
        credential_name = f"{credential_name}-{secrets.token_hex(4)}"
        cred = None

    created_credential = False
    if not cred:
        token_value = token_value or secrets.token_urlsafe(32)
        cred_payload = {
            "name": credential_name,
            "description": f"Token credential for Event Stream {args.name}",
            "credential_type_id": 8,
            "organization_id": organization_id,
            "inputs": {
                "auth_type": "token",
                "token": token_value,
                "http_header_key": args.http_header_key,
            },
        }
        status, _, body = _request(
            opener,
            method="POST",
            url=f"{args.base_url}/api/eda/v1/eda-credentials/",
            headers={
                **common_headers,
                "Content-Type": "application/json",
            },
            data=json.dumps(cred_payload).encode("utf-8"),
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            sys.stderr.write(f"Failed to create EDA credential: HTTP {status}\n")
            sys.stderr.write(body.decode("utf-8", errors="replace")[:1000] + "\n")
            return 4
        cred = _parse_json(body)
        created_credential = True
    if not isinstance(cred, dict) or not isinstance(cred.get("id"), int):
        sys.stderr.write("EDA credential creation succeeded but returned unexpected payload.\n")
        print(_json_dumps({"credential": cred}))
        return 4
    eda_credential_id = int(cred["id"])

    eda_event_streams_url = f"{args.base_url}/api/eda/v1/event-streams/"
    payload = {
        "name": args.name,
        "description": args.description,
        "test_mode": bool(args.test_mode),
        "organization_id": organization_id,
        "eda_credential_id": eda_credential_id,
    }
    data = json.dumps(payload).encode("utf-8")
    status, _, body = _request(
        opener,
        method="POST",
        url=eda_event_streams_url,
        headers={
            **common_headers,
            "Content-Type": "application/json",
        },
        data=data,
        timeout_s=args.timeout,
    )
    body_text = body.decode("utf-8", errors="replace") if body else ""
    already_exists = status in (400, 409) and "already exists" in body_text.lower()
    if status == 409 or already_exists:
        sys.stderr.write("Event Stream already exists (HTTP 409). Fetching existing list...\n")
    elif status < 200 or status >= 400:
        sys.stderr.write(f"Failed to create Event Stream: HTTP {status}\n")
        sys.stderr.write(body_text[:1000] + "\n")
        return 5
    else:
        created = _parse_json(body)
        if isinstance(created, dict):
            url = _best_url_from_event_stream(created)
            out: dict[str, Any] = {"created": created, "webhook_url_guess": url}
            if token_value and args.write_token:
                _write_secret_file(args.write_token, token_value)
                out["token_auth"] = {"header": args.http_header_key, "token_file": args.write_token}
            elif token_value:
                out["token_auth"] = {"header": args.http_header_key, "token": token_value}
            else:
                out["token_auth"] = {"header": args.http_header_key, "token": "<unknown: token is stored encrypted in EDA>"}
            print(_json_dumps(out))
            return 0

    # If we got here, try to list and locate by name.
    query = urllib.parse.urlencode({"test_mode": str(bool(args.test_mode)).lower()})
    list_url = f"{eda_event_streams_url}?{query}"
    status, _, body = _request(
        opener,
        method="GET",
        url=list_url,
        headers=common_headers,
        timeout_s=args.timeout,
    )
    if status < 200 or status >= 400:
        sys.stderr.write(f"Failed to list Event Streams: HTTP {status}\n")
        sys.stderr.write(body.decode("utf-8", errors="replace")[:1000] + "\n")
        return 6

    listed = _parse_json(body)
    results = []
    if isinstance(listed, dict) and isinstance(listed.get("results"), list):
        results = listed["results"]
    elif isinstance(listed, list):
        results = listed

    match = next(
        (r for r in results if isinstance(r, dict) and r.get("name") == args.name),
        None,
    )
    if not isinstance(match, dict):
        sys.stderr.write(f"Could not find Event Stream named {args.name!r} in list.\n")
        print(_json_dumps({"list": listed}))
        return 7

    # If we generated a new token/credential (rotate-token) and the event stream already existed,
    # re-point it to the new credential so the returned token is actually usable.
    if args.rotate_token and created_credential:
        es_id = match.get("id")
        if not isinstance(es_id, int):
            sys.stderr.write("Event Stream exists but has no integer id; cannot rotate credential.\n")
            print(_json_dumps({"existing": match}))
            return 7
        patch_url = f"{args.base_url}/api/eda/v1/event-streams/{es_id}/"
        patch_payload = {"eda_credential_id": eda_credential_id, "organization_id": organization_id}
        status, _, body = _request(
            opener,
            method="PATCH",
            url=patch_url,
            headers={**common_headers, "Content-Type": "application/json"},
            data=json.dumps(patch_payload).encode("utf-8"),
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            sys.stderr.write(f"Failed to rotate Event Stream credential: HTTP {status}\n")
            sys.stderr.write(body.decode("utf-8", errors="replace")[:1000] + "\n")
            return 7
        match = _parse_json(body) if body else match

    url = _best_url_from_event_stream(match)
    out = {"existing": match, "webhook_url_guess": url}
    if token_value and args.write_token:
        _write_secret_file(args.write_token, token_value)
        out["token_auth"] = {"header": args.http_header_key, "token_file": args.write_token}
    elif token_value:
        out["token_auth"] = {"header": args.http_header_key, "token": token_value}
    else:
        out["token_auth"] = {
            "header": args.http_header_key,
            "token": "<unknown: credential already existed; token is stored encrypted in EDA>",
        }
    print(_json_dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
