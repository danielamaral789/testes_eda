#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_placeholders(obj: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(obj, str):
        for key, value in placeholders.items():
            obj = obj.replace("${" + key + "}", value)
        return obj
    if isinstance(obj, list):
        return [_apply_placeholders(v, placeholders) for v in obj]
    if isinstance(obj, dict):
        return {k: _apply_placeholders(v, placeholders) for k, v in obj.items()}
    return obj


def _parse_header_values(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in values:
        if ":" not in raw:
            raise ValueError(f'Invalid header (expected "Key: Value"): {raw!r}')
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid header key: {raw!r}")
        headers[key] = value
    return headers


@dataclass(frozen=True)
class HmacConfig:
    secret: bytes
    header: str
    prefix: str
    algo: str


def _hmac_digest(payload_bytes: bytes, cfg: HmacConfig) -> str:
    if cfg.algo == "sha256":
        digest = hmac.new(cfg.secret, payload_bytes, hashlib.sha256).hexdigest()
    elif cfg.algo == "sha1":
        digest = hmac.new(cfg.secret, payload_bytes, hashlib.sha1).hexdigest()
    else:
        raise ValueError(f"Unsupported HMAC algo: {cfg.algo}")
    return cfg.prefix + digest


def _build_default_event(sequence: int) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "sent_at": _utc_now_iso(),
        "sequence": sequence,
        "source": "testes_eda",
        "type": "synthetic",
        "payload": {
            "severity": random.choice(["low", "medium", "high"]),
            "host": random.choice(["web-1", "web-2", "db-1"]),
            "message": "Synthetic event for EDA webhook testing",
        },
    }


def _request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout_s: float,
    insecure: bool,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url=url, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)

    context = None
    if insecure and url.lower().startswith("https://"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=context) as resp:
            status = int(getattr(resp, "status", 200))
            resp_headers = {k: v for (k, v) in resp.headers.items()}
            resp_body = resp.read() if method.upper() != "HEAD" else b""
            return status, resp_headers, resp_body
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        resp_headers = {k: v for (k, v) in (e.headers.items() if e.headers else [])}
        resp_body = e.read() if hasattr(e, "read") else b""
        return status, resp_headers, resp_body


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Send synthetic JSON events to an EDA webhook/event-stream URL."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("EDA_WEBHOOK_URL", ""),
        help="Webhook URL (or set EDA_WEBHOOK_URL).",
    )
    parser.add_argument(
        "--method",
        default="POST",
        choices=["POST", "PUT", "PATCH"],
        help="HTTP method.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='Extra header, repeatable. Format: "Key: Value".',
    )
    parser.add_argument(
        "--template",
        help="JSON file template; supports ${uuid}, ${now}, ${sequence}.",
    )
    parser.add_argument(
        "--data",
        help="Inline JSON string for the request body (overrides --template).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="How many events to send (0 = forever).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between events.",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="Random jitter added to interval (0..jitter seconds).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate validation (use only for testing).",
    )
    parser.add_argument(
        "--hmac-secret",
        default=os.environ.get("EDA_WEBHOOK_HMAC_SECRET", ""),
        help="Optional shared secret for HMAC header (or set EDA_WEBHOOK_HMAC_SECRET).",
    )
    parser.add_argument(
        "--hmac-header",
        default=os.environ.get("EDA_WEBHOOK_HMAC_HEADER", ""),
        help="Header name to send the HMAC digest in (e.g. X-Hub-Signature-256).",
    )
    parser.add_argument(
        "--hmac-prefix",
        default=os.environ.get("EDA_WEBHOOK_HMAC_PREFIX", ""),
        help='Digest prefix (e.g. "sha256=").',
    )
    parser.add_argument(
        "--hmac-algo",
        default=os.environ.get("EDA_WEBHOOK_HMAC_ALGO", "sha256"),
        choices=["sha256", "sha1"],
        help="HMAC algorithm.",
    )
    args = parser.parse_args(argv)

    if not args.url:
        parser.error("Missing --url (or env EDA_WEBHOOK_URL).")

    headers = _parse_header_values(args.header)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")

    hmac_cfg: HmacConfig | None = None
    if args.hmac_secret or args.hmac_header:
        if not (args.hmac_secret and args.hmac_header):
            parser.error("--hmac-secret and --hmac-header must be set together.")
        hmac_cfg = HmacConfig(
            secret=args.hmac_secret.encode("utf-8"),
            header=args.hmac_header,
            prefix=args.hmac_prefix,
            algo=args.hmac_algo,
        )

    total = args.count
    sequence = 1
    while total == 0 or sequence <= total:
        placeholders = {
            "uuid": str(uuid.uuid4()),
            "now": _utc_now_iso(),
            "sequence": str(sequence),
        }

        if args.data:
            event_obj = json.loads(args.data)
        elif args.template:
            event_obj = _load_json(args.template)
        else:
            event_obj = _build_default_event(sequence)

        event_obj = _apply_placeholders(event_obj, placeholders)
        payload_str = _json_dumps(event_obj)
        payload_bytes = payload_str.encode("utf-8")

        request_headers = dict(headers)
        if hmac_cfg is not None:
            request_headers[hmac_cfg.header] = _hmac_digest(payload_bytes, hmac_cfg)

        status, resp_headers, resp_body = _request(
            url=args.url,
            method=args.method,
            headers=request_headers,
            body=payload_bytes,
            timeout_s=args.timeout,
            insecure=args.insecure,
        )

        body_preview = resp_body[:500]
        try:
            decoded_preview = body_preview.decode("utf-8", errors="replace")
        except Exception:
            decoded_preview = repr(body_preview)

        print(
            _json_dumps(
                {
                    "sequence": sequence,
                    "request": {"url": args.url, "bytes": len(payload_bytes)},
                    "response": {
                        "status": status,
                        "content_type": resp_headers.get("Content-Type", ""),
                        "body_preview": decoded_preview,
                    },
                }
            )
        )

        sequence += 1
        if total != 0 and sequence > total:
            break
        sleep_for = max(0.0, args.interval + (random.random() * args.jitter))
        time.sleep(sleep_for)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

