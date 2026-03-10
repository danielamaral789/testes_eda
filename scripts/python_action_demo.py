#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _priority_from_severity(severity: str) -> str:
    normalized = severity.strip().lower()
    if normalized == "high":
        return "p1"
    if normalized == "medium":
        return "p2"
    if normalized == "low":
        return "p3"
    return "unknown"


def _append_jsonl(path: str, payload: dict[str, object]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="EDA python action demo (enrich + print JSON).")
    p.add_argument("--event-id", default="")
    p.add_argument("--host", default="")
    p.add_argument("--severity", default="")
    p.add_argument("--message", default="")
    p.add_argument("--sent-at", default="")
    p.add_argument("--source", default="")
    p.add_argument("--event-type", default="")
    p.add_argument("--sequence", default="")
    p.add_argument("--output-file", default=os.environ.get("EDA_PYTHON_ACTION_LOG", ""))
    args = p.parse_args(argv)

    host = _normalize_text(args.host)
    severity = _normalize_text(args.severity).lower()
    message = _normalize_text(args.message)
    source = _normalize_text(args.source)
    event_type = _normalize_text(args.event_type)
    sequence = _normalize_text(args.sequence)

    fingerprint_src = "|".join(
        [args.event_id, host, severity, args.sent_at, message, source, event_type, sequence]
    ).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_src).hexdigest()
    priority = _priority_from_severity(severity)
    summary = f"{priority} event on {host or 'unknown-host'}"

    out = {
        "kind": "python_action_demo",
        "seen_at": _utc_now_iso(),
        "event_id": args.event_id,
        "host": host,
        "severity": severity,
        "sent_at": args.sent_at,
        "message": message,
        "source": source,
        "event_type": event_type,
        "sequence": sequence,
        "priority": priority,
        "summary": summary,
        "fingerprint_sha256": fingerprint,
    }
    if args.output_file:
        _append_jsonl(args.output_file, out)
        out["written_to"] = args.output_file
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
