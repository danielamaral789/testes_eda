#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)


def _request(
    opener: urllib.request.OpenerDirector,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: Any | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper())
    for k, v in headers.items():
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
        raise RuntimeError(f"Failed to GET login URL: HTTP {status}: {body[:200]!r}")

    csrftoken = next((c.value for c in cj if c.name == "csrftoken"), "")
    if not csrftoken:
        raise RuntimeError("No csrftoken cookie found; cannot log in.")

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
        if e.code in (400, 401, 403):
            raise RuntimeError("Login failed (check username/password/permissions).") from e
        raise RuntimeError(f"Login failed: HTTP {e.code}: {body[:200]!r}") from e

    csrftoken = next((c.value for c in cj if c.name == "csrftoken"), csrftoken)
    return opener, cj, csrftoken


def _delete_by_id(
    opener: urllib.request.OpenerDirector,
    *,
    url: str,
    headers: dict[str, str],
    timeout_s: float,
) -> tuple[bool, int]:
    status, _, _ = _request(opener, method="DELETE", url=url, headers=headers, timeout_s=timeout_s)
    if status in (200, 202, 204):
        return True, status
    if status == 404:
        return False, status
    raise RuntimeError(f"DELETE {url} failed: HTTP {status}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Cleanup the EDA lab resources created by create_eda_event_stream.py + create_eda_hello_webhook_stack.py."
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="AAP/EDA base URL, e.g. https://... (required).",
    )
    parser.add_argument("--username", default="admin", help="Username (default: admin).")
    parser.add_argument("--password", default="", help="Password (if empty, prompt).")
    parser.add_argument("--organization", default="Default", help='Organization name (default: "Default").')
    parser.add_argument("--event-stream", default="lab-webhook", help="Event Stream name to delete.")
    parser.add_argument("--activation", default="activation-hello-webhook", help="Activation name to delete.")
    parser.add_argument("--project-name", default="hello-webhook-project", help="Project name to delete.")
    parser.add_argument("--decision-environment", default="de-hello-webhook", help="Decision environment name to delete.")
    parser.add_argument(
        "--delete-token-creds",
        action="store_true",
        help="Also delete matching Token Event Stream credentials (<event-stream>-token*).",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only).")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    if not base_url:
        parser.error("Missing --base-url")

    password = args.password or getpass.getpass(f"Password for {args.username}: ")

    opener, _, csrftoken = _login(
        base_url=base_url,
        username=args.username,
        password=password,
        timeout_s=args.timeout,
        insecure=args.insecure,
    )

    api_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }

    out: dict[str, Any] = {"base_url": base_url, "deleted": {}, "skipped": {}}

    # Resolve org just to validate access.
    orgs = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/organizations/?name={urllib.parse.quote(args.organization)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    org = _first_result(orgs)
    if not org or not isinstance(org.get("id"), int):
        sys.stderr.write(f"Organization {args.organization!r} not found.\n")
        return 2

    # 1) Activation
    activations = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/activations/?name={urllib.parse.quote(args.activation)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    activation = _first_result(activations) or _find_by_name(activations, args.activation)
    if isinstance(activation, dict) and isinstance(activation.get("id"), int):
        activation_id = int(activation["id"])
        deleted, status = _delete_by_id(
            opener,
            url=f"{base_url}/api/eda/v1/activations/{activation_id}/",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        if deleted:
            out["deleted"]["activation"] = {"id": activation_id, "http": status, "name": args.activation}
    else:
        out["skipped"]["activation"] = {"name": args.activation, "reason": "not found"}

    # 2) Event Stream
    streams = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/event-streams/?test_mode=false&page=1&page_size=200",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    event_stream = _find_by_name(streams.get("results", []), args.event_stream)
    if isinstance(event_stream, dict) and isinstance(event_stream.get("id"), int):
        es_id = int(event_stream["id"])
        event_stream_url = f"{base_url}/api/eda/v1/event-streams/{es_id}/"
        try:
            deleted, status = _delete_by_id(
                opener,
                url=event_stream_url,
                headers=api_headers,
                timeout_s=args.timeout,
            )
            if deleted:
                out["deleted"]["event_stream"] = {"id": es_id, "http": status, "name": args.event_stream}
        except RuntimeError as e:
            # 409 here usually means some activation still references this stream.
            if "HTTP 409" not in str(e):
                raise
            # Find and delete any activations that reference this stream, then retry once.
            acts = _get_json(
                opener,
                url=f"{base_url}/api/eda/v1/activations/?page=1&page_size=200",
                headers=api_headers,
                timeout_s=args.timeout,
            )
            act_results = acts.get("results", []) if isinstance(acts, dict) else []
            removed_refs: list[dict[str, Any]] = []
            for a in act_results:
                if not isinstance(a, dict) or not isinstance(a.get("id"), int):
                    continue
                act_id = int(a["id"])
                detail = _get_json(
                    opener,
                    url=f"{base_url}/api/eda/v1/activations/{act_id}/",
                    headers=api_headers,
                    timeout_s=args.timeout,
                )
                mappings = detail.get("source_mappings", []) if isinstance(detail, dict) else []
                uses_stream = any(
                    isinstance(m, dict) and m.get("event_stream_id") == es_id for m in (mappings or [])
                )
                if not uses_stream:
                    continue
                deleted, status = _delete_by_id(
                    opener,
                    url=f"{base_url}/api/eda/v1/activations/{act_id}/",
                    headers=api_headers,
                    timeout_s=args.timeout,
                )
                if deleted:
                    removed_refs.append({"id": act_id, "http": status, "name": str(detail.get("name", ""))})
            if removed_refs:
                out["deleted"]["activations_referencing_event_stream"] = removed_refs

            deleted, status = _delete_by_id(
                opener,
                url=event_stream_url,
                headers=api_headers,
                timeout_s=args.timeout,
            )
            if deleted:
                out["deleted"]["event_stream"] = {"id": es_id, "http": status, "name": args.event_stream}
    else:
        out["skipped"]["event_stream"] = {"name": args.event_stream, "reason": "not found"}

    # 3) Decision Environment
    de_list = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/decision-environments/?name={urllib.parse.quote(args.decision_environment)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    de = _first_result(de_list)
    if isinstance(de, dict) and isinstance(de.get("id"), int):
        de_id = int(de["id"])
        deleted, status = _delete_by_id(
            opener,
            url=f"{base_url}/api/eda/v1/decision-environments/{de_id}/",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        if deleted:
            out["deleted"]["decision_environment"] = {
                "id": de_id,
                "http": status,
                "name": args.decision_environment,
            }
    else:
        out["skipped"]["decision_environment"] = {"name": args.decision_environment, "reason": "not found"}

    # 4) Project
    projects = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/projects/?name={urllib.parse.quote(args.project_name)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    project = _first_result(projects)
    if isinstance(project, dict) and isinstance(project.get("id"), int):
        project_id = int(project["id"])
        deleted, status = _delete_by_id(
            opener,
            url=f"{base_url}/api/eda/v1/projects/{project_id}/",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        if deleted:
            out["deleted"]["project"] = {"id": project_id, "http": status, "name": args.project_name}
    else:
        out["skipped"]["project"] = {"name": args.project_name, "reason": "not found"}

    # 5) Token creds (optional)
    if args.delete_token_creds:
        prefix = f"{args.event_stream}-token"
        creds = _get_json(
            opener,
            url=f"{base_url}/api/eda/v1/eda-credentials/?page=1&page_size=200",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        results = creds.get("results", []) if isinstance(creds, dict) else []
        to_delete = [
            c
            for c in results
            if isinstance(c, dict)
            and isinstance(c.get("id"), int)
            and isinstance(c.get("name"), str)
            and str(c["name"]).startswith(prefix)
        ]
        deleted_creds: list[dict[str, Any]] = []
        for c in to_delete:
            cred_id = int(c["id"])
            deleted, status = _delete_by_id(
                opener,
                url=f"{base_url}/api/eda/v1/eda-credentials/{cred_id}/",
                headers=api_headers,
                timeout_s=args.timeout,
            )
            if deleted:
                deleted_creds.append({"id": cred_id, "http": status, "name": str(c.get("name"))})
        if deleted_creds:
            out["deleted"]["eda_credentials"] = deleted_creds
        else:
            out["skipped"]["eda_credentials"] = {"prefix": prefix, "reason": "none found"}

    print(_json_dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
