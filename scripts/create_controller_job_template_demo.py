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
) -> tuple[int, bytes]:
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


def _cookie_value(cj: CookieJar, name: str) -> str:
    return next((c.value for c in cj if c.name == name), "")


def _get_json(opener, url: str, headers: dict[str, str], timeout_s: float) -> Any:
    status, body = _request(opener, method="GET", url=url, headers=headers, timeout_s=timeout_s)
    if status < 200 or status >= 400:
        raise RuntimeError(f"GET {url} failed: HTTP {status}: {body[:300]!r}")
    return _parse_json(body)


def _first_result(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict) and isinstance(obj.get("results"), list) and obj["results"]:
        first = obj["results"][0]
        return first if isinstance(first, dict) else None
    return None


def _find_by_name(obj: Any, name: str) -> dict[str, Any] | None:
    results = obj.get("results", []) if isinstance(obj, dict) else []
    for item in results:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Create a minimal Controller demo: Project + Inventory(localhost) + Job Template.")
    p.add_argument("--base-url", default=os.environ.get("EDA_BASE_URL", "").rstrip("/"), help="AAP base URL (Gateway).")
    p.add_argument("--username", default=os.environ.get("EDA_USERNAME", "admin"))
    p.add_argument("--password", default=os.environ.get("EDA_PASSWORD", ""))
    p.add_argument("--organization", default="Default")
    p.add_argument("--scm-url", default="https://github.com/danielamaral789/testes_eda.git")
    p.add_argument("--scm-branch", default="main")
    p.add_argument("--project-name", default="controller-testes-eda-project")
    p.add_argument("--inventory-name", default="eda-demo-inventory")
    p.add_argument("--job-template", default="Demo - Remediate Host")
    p.add_argument("--playbook", default="playbooks/remediate_host.yml")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--wait", type=int, default=180, help="Wait for project update (seconds).")
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
    status, body = _request(opener, method="GET", url=login_url, headers={"Accept": "application/json,text/*"}, timeout_s=args.timeout)
    if status < 200 or status >= 400:
        sys.stderr.write(f"GET login failed: HTTP {status}: {body[:200]!r}\n")
        return 2

    csrftoken = _cookie_value(cj, "csrftoken")
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

    csrftoken = _cookie_value(cj, "csrftoken") or csrftoken
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken,
        "Origin": args.base_url,
        "Referer": f"{args.base_url}/",
    }

    controller_api = f"{args.base_url}/api/controller/v2"

    # Organization
    orgs = _get_json(opener, f"{controller_api}/organizations/?name={urllib.parse.quote(args.organization)}", headers, args.timeout)
    org = _first_result(orgs)
    if not org or not isinstance(org.get("id"), int):
        raise RuntimeError(f"Controller organization {args.organization!r} not found.")
    org_id = int(org["id"])

    # Project (Controller)
    projects = _get_json(opener, f"{controller_api}/projects/?name={urllib.parse.quote(args.project_name)}", headers, args.timeout)
    project = _first_result(projects)
    if not project:
        payload = {
            "name": args.project_name,
            "organization": org_id,
            "scm_type": "git",
            "scm_url": args.scm_url,
            "scm_branch": args.scm_branch,
            "scm_update_on_launch": True,
        }
        status, body = _request(opener, method="POST", url=f"{controller_api}/projects/", headers=headers, payload=payload, timeout_s=args.timeout)
        if status < 200 or status >= 400:
            raise RuntimeError(f"Create controller project failed: HTTP {status}: {body[:500]!r}")
        project = _parse_json(body)
    project_id = int(project["id"])

    # Trigger project update
    _request(opener, method="POST", url=f"{controller_api}/projects/{project_id}/update/", headers=headers, timeout_s=args.timeout)
    deadline = time.time() + max(0, args.wait)
    while time.time() < deadline:
        project = _get_json(opener, f"{controller_api}/projects/{project_id}/", headers, args.timeout)
        if project.get("status") in ("successful", "failed", "error"):
            break
        time.sleep(2)

    # Inventory + localhost
    inventories = _get_json(opener, f"{controller_api}/inventories/?name={urllib.parse.quote(args.inventory_name)}", headers, args.timeout)
    inventory = _first_result(inventories)
    if not inventory:
        status, body = _request(
            opener,
            method="POST",
            url=f"{controller_api}/inventories/",
            headers=headers,
            payload={"name": args.inventory_name, "organization": org_id},
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise RuntimeError(f"Create inventory failed: HTTP {status}: {body[:500]!r}")
        inventory = _parse_json(body)
    inv_id = int(inventory["id"])

    # Ensure localhost host exists
    hosts = _get_json(opener, f"{controller_api}/hosts/?name=localhost&inventory={inv_id}", headers, args.timeout)
    host = _first_result(hosts)
    if not host:
        status, body = _request(
            opener,
            method="POST",
            url=f"{controller_api}/hosts/",
            headers=headers,
            payload={
                "name": "localhost",
                "inventory": inv_id,
                "variables": "ansible_connection: local\n",
            },
            timeout_s=args.timeout,
        )
        if status < 200 or status >= 400:
            raise RuntimeError(f"Create host failed: HTTP {status}: {body[:500]!r}")
        host = _parse_json(body)

    # Job Template
    jts = _get_json(opener, f"{controller_api}/job_templates/?name={urllib.parse.quote(args.job_template)}", headers, args.timeout)
    jt = _first_result(jts)
    jt_payload = {
        "name": args.job_template,
        "organization": org_id,
        "job_type": "run",
        "inventory": inv_id,
        "project": project_id,
        "playbook": args.playbook,
        "ask_variables_on_launch": True,
    }
    if not jt:
        status, body = _request(opener, method="POST", url=f"{controller_api}/job_templates/", headers=headers, payload=jt_payload, timeout_s=args.timeout)
        if status < 200 or status >= 400:
            raise RuntimeError(f"Create job template failed: HTTP {status}: {body[:500]!r}")
        jt = _parse_json(body)
    else:
        jt_id = int(jt["id"])
        _request(opener, method="PATCH", url=f"{controller_api}/job_templates/{jt_id}/", headers=headers, payload=jt_payload, timeout_s=args.timeout)
        jt = _get_json(opener, f"{controller_api}/job_templates/{jt_id}/", headers, args.timeout)

    out = {"organization": org, "project": project, "inventory": inventory, "job_template": jt}
    print(_json_dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

