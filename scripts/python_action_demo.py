#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="EDA python action demo (enrich + print JSON).")
    p.add_argument("--event-id", default="")
    p.add_argument("--host", default="")
    p.add_argument("--severity", default="")
    p.add_argument("--message", default="")
    p.add_argument("--sent-at", default="")
    args = p.parse_args(argv)

    fingerprint_src = "|".join([args.event_id, args.host, args.severity, args.sent_at, args.message]).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_src).hexdigest()

    out = {
        "kind": "python_action_demo",
        "seen_at": _utc_now_iso(),
        "event_id": args.event_id,
        "host": args.host,
        "severity": args.severity,
        "sent_at": args.sent_at,
        "message": args.message,
        "fingerprint_sha256": fingerprint,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

