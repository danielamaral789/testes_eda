#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import time
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
) -> tuple[urllib.request.OpenerDirector, CookieJar, str]:
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    login_url = f"{base_url}/api/gateway/v1/login/"
    status, _, _ = _request(
        opener,
        method="GET",
        url=login_url,
        headers={"Accept": "application/json,text/*"},
        timeout_s=timeout_s,
    )
    if status < 200 or status >= 400:
        raise RuntimeError(f"Failed to GET login URL: HTTP {status}")

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
        raise RuntimeError(f"Login failed: HTTP {e.code}: {body[:500]!r}") from e

    csrftoken = next((c.value for c in cj if c.name == "csrftoken"), csrftoken)
    return opener, cj, csrftoken


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Create a minimal EDA stack: Project + Decision Environment + Activation wired to an existing Event Stream."
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="AAP/EDA base URL, e.g. https://... (required).",
    )
    parser.add_argument("--username", default="admin", help="Username (default: admin).")
    parser.add_argument("--password", default="", help="Password (if empty, prompt).")
    parser.add_argument(
        "--organization",
        default="Default",
        help='Organization name (default: "Default").',
    )
    parser.add_argument(
        "--event-stream",
        default="lab-webhook",
        help='Event Stream name to map (default: "lab-webhook").',
    )
    parser.add_argument(
        "--project-name",
        default="hello-webhook-project",
        help="Project name to create/use.",
    )
    parser.add_argument(
        "--project-url",
        default="https://github.com/ansible/event-driven-ansible.git",
        help="Git URL for the project (public by default).",
    )
    parser.add_argument("--project-branch", default="main", help="Git branch (default: main).")
    parser.add_argument(
        "--rulebook",
        default="demo_webhook_rulebook.yml",
        help="Rulebook file name inside the project (default: demo_webhook_rulebook.yml).",
    )
    parser.add_argument(
        "--decision-environment",
        default="de-hello-webhook",
        help="Decision environment name to create/use.",
    )
    parser.add_argument(
        "--de-image",
        default="quay.io/ansible/ansible-rulebook:v1.2.1",
        help="Decision environment image_url (default: quay.io/ansible/ansible-rulebook:v1.2.1).",
    )
    parser.add_argument(
        "--activation",
        default="activation-hello-webhook",
        help="Activation name to create/use.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds.")
    parser.add_argument(
        "--wait",
        type=int,
        default=180,
        help="Seconds to wait for project import / activation start (default: 180).",
    )
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
    )

    api_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }

    # Organization
    orgs = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/organizations/?name={urllib.parse.quote(args.organization)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    org = _first_result(orgs)
    if not org or not isinstance(org.get("id"), int):
        raise RuntimeError(f"Organization {args.organization!r} not found.")
    organization_id = int(org["id"])

    # Event Stream
    streams = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/event-streams/?test_mode=false&page=1&page_size=200",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    event_stream = _find_by_name(streams.get("results", []), args.event_stream)
    if not event_stream or not isinstance(event_stream.get("id"), int):
        raise RuntimeError(
            f"Event Stream {args.event_stream!r} not found. Create it first with scripts/create_eda_event_stream.py"
        )
    event_stream_id = int(event_stream["id"])
    event_stream_name = str(event_stream["name"])

    # Decision environment
    de_list = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/decision-environments/?name={urllib.parse.quote(args.decision_environment)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    de = _first_result(de_list)
    if not de:
        status, _, body = _request(
            opener,
            method="POST",
            url=f"{base_url}/api/eda/v1/decision-environments/",
            headers=api_headers,
            payload={
                "name": args.decision_environment,
                "description": "Decision environment for hello-webhook lab",
                "image_url": args.de_image,
                "organization_id": organization_id,
                "pull_policy": "missing",
            },
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise RuntimeError(f"Failed to create decision environment: HTTP {status}: {body[:500]!r}")
        de = _parse_json(body)
    else:
        # Keep DE aligned with the desired image (safe in a lab; avoids the xxhash issue on some tags).
        _request(
            opener,
            method="PATCH",
            url=f"{base_url}/api/eda/v1/decision-environments/{int(de['id'])}/",
            headers=api_headers,
            payload={"image_url": args.de_image},
            timeout_s=args.timeout,
        )

    if not isinstance(de, dict) or not isinstance(de.get("id"), int):
        raise RuntimeError("Decision environment resolution failed.")
    decision_environment_id = int(de["id"])

    # Project
    proj_list = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/projects/?name={urllib.parse.quote(args.project_name)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    project = _first_result(proj_list)
    if not project:
        status, _, body = _request(
            opener,
            method="POST",
            url=f"{base_url}/api/eda/v1/projects/",
            headers=api_headers,
            payload={
                "name": args.project_name,
                "description": "Project with demo webhook rulebook",
                "organization_id": organization_id,
                "url": args.project_url,
                "scm_type": "git",
                "scm_branch": args.project_branch,
                "verify_ssl": True,
            },
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise RuntimeError(f"Failed to create project: HTTP {status}: {body[:500]!r}")
        project = _parse_json(body)

    if not isinstance(project, dict) or not isinstance(project.get("id"), int):
        raise RuntimeError("Project resolution failed.")
    project_id = int(project["id"])

    # Wait for project import so rulebooks exist.
    deadline = time.time() + max(0, args.wait)
    while time.time() < deadline:
        project = _get_json(
            opener,
            url=f"{base_url}/api/eda/v1/projects/{project_id}/",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        state = project.get("import_state")
        if state in ("completed", "successful"):
            break
        if state in ("failed", "error"):
            raise RuntimeError(f"Project import failed: {project.get('import_error')}")
        time.sleep(2)

    # Rulebook
    rulebooks = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/rulebooks/?project_id={project_id}&page_size=200",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    rulebook = _find_by_name(rulebooks.get("results", []), args.rulebook)
    if not rulebook or not isinstance(rulebook.get("id"), int):
        raise RuntimeError(f"Rulebook {args.rulebook!r} not found in project {args.project_name!r}.")
    rulebook_id = int(rulebook["id"])

    # Rulebook sources (needed for source_mappings schema)
    sources = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/rulebooks/{rulebook_id}/sources/?page=1&page_size=200",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    src = _first_result(sources)
    if not src or not isinstance(src.get("name"), str) or not isinstance(src.get("rulebook_hash"), str):
        raise RuntimeError("Could not resolve rulebook sources for source_mappings.")
    source_name = src["name"]
    rulebook_hash = src["rulebook_hash"]

    source_mappings = (
        f"- source_name: {source_name}\n"
        f"  event_stream_id: '{event_stream_id}'\n"
        f"  event_stream_name: {event_stream_name}\n"
        f"  rulebook_hash: {rulebook_hash}\n"
    )

    # Activation
    act_list = _get_json(
        opener,
        url=f"{base_url}/api/eda/v1/activations/?name={urllib.parse.quote(args.activation)}",
        headers=api_headers,
        timeout_s=args.timeout,
    )
    activation = _first_result(act_list)
    if not activation:
        status, _, body = _request(
            opener,
            method="POST",
            url=f"{base_url}/api/eda/v1/activations/",
            headers=api_headers,
            payload={
                "name": args.activation,
                "description": f"Prints all events from Event Stream {event_stream_name}",
                "organization_id": organization_id,
                "decision_environment_id": decision_environment_id,
                "rulebook_id": rulebook_id,
                "is_enabled": True,
                "restart_policy": "always",
                "log_level": "info",
                "source_mappings": source_mappings,
            },
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise RuntimeError(f"Failed to create activation: HTTP {status}: {body[:500]!r}")
        activation = _parse_json(body)
    else:
        # Best-effort update if it already exists.
        _request(
            opener,
            method="PATCH",
            url=f"{base_url}/api/eda/v1/activations/{int(activation['id'])}/",
            headers=api_headers,
            payload={
                "decision_environment_id": decision_environment_id,
                "rulebook_id": rulebook_id,
                "source_mappings": source_mappings,
                "is_enabled": True,
            },
            timeout_s=args.timeout,
        )

    activation_id = int(activation["id"])

    # Wait for activation to start.
    deadline = time.time() + max(0, args.wait)
    while time.time() < deadline:
        activation = _get_json(
            opener,
            url=f"{base_url}/api/eda/v1/activations/{activation_id}/",
            headers=api_headers,
            timeout_s=args.timeout,
        )
        status = activation.get("status")
        if status in ("running", "failed", "error", "unresponsive", "stopped"):
            break
        time.sleep(2)

    print(
        _json_dumps(
            {
                "organization_id": organization_id,
                "event_stream": {
                    "id": event_stream_id,
                    "name": event_stream_name,
                    "url": event_stream.get("url"),
                },
                "project": {"id": project_id, "name": args.project_name, "url": args.project_url},
                "decision_environment": {
                    "id": decision_environment_id,
                    "name": args.decision_environment,
                    "image_url": args.de_image,
                },
                "rulebook": {"id": rulebook_id, "name": args.rulebook},
                "activation": {
                    "id": activation_id,
                    "name": args.activation,
                    "status": activation.get("status"),
                    "status_message": activation.get("status_message"),
                    "source_mappings": source_mappings,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
