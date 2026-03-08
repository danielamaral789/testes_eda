#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import getpass


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


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return d0 + d1


@dataclass
class Bucket:
    count: int = 0
    ok: int = 0
    errors: int = 0
    latencies_ms_all: list[float] = field(default_factory=list)
    latencies_ms_ok: list[float] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)


class RateLimiter:
    def __init__(self, rate_at: Callable[[float], float], start_monotonic: float) -> None:
        self._rate_at = rate_at
        self._next_time = start_monotonic
        self._lock = threading.Lock()

    def wait_turn(self, t_s: float) -> None:
        rate = float(self._rate_at(max(0.0, t_s)))
        if rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            scheduled = self._next_time
            if scheduled < now:
                scheduled = now
            self._next_time = scheduled + (1.0 / rate)
        now = time.monotonic()
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)


def _request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout_s: float,
    insecure: bool,
) -> tuple[int, dict[str, str], bytes, str | None]:
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
            return status, resp_headers, resp_body, None
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        resp_headers = {k: v for (k, v) in (e.headers.items() if e.headers else [])}
        resp_body = e.read() if hasattr(e, "read") else b""
        return status, resp_headers, resp_body, f"HTTPError {status}"
    except Exception as e:
        return 0, {}, b"", f"{type(e).__name__}: {e}"


def _build_event_payload_bytes(
    *,
    template_obj: Any | None,
    inline_obj: Any | None,
    vary: bool,
    sequence: int,
) -> bytes:
    if inline_obj is not None:
        obj = inline_obj
    elif template_obj is not None:
        obj = template_obj
    else:
        obj = {
            "id": str(uuid.uuid4()),
            "sent_at": _utc_now_iso(),
            "sequence": sequence,
            "source": "testes_eda_load_test",
            "type": "synthetic",
            "payload": {
                "severity": random.choice(["low", "medium", "high"]),
                "host": random.choice(["web-1", "web-2", "db-1"]),
                "message": "Load test event",
            },
        }

    if vary:
        placeholders = {
            "uuid": str(uuid.uuid4()),
            "now": _utc_now_iso(),
            "sequence": str(sequence),
        }
        obj = _apply_placeholders(obj, placeholders)

    return _json_dumps(obj).encode("utf-8")


def _write_html_report(
    *,
    out_path: str,
    title: str,
    summary: dict[str, Any],
    series: list[dict[str, Any]],
) -> None:
    payload = {"summary": summary, "series": series}
    payload_json = json.dumps(payload, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        --bg: #0b0f14;
        --panel: #121823;
        --text: #e6edf3;
        --muted: #9fb0c0;
        --accent: #7aa2ff;
        --danger: #ff6b6b;
        --ok: #40c463;
        --border: #223047;
      }}
      body {{
        margin: 0;
        background: var(--bg);
        color: var(--text);
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
      }}
      .wrap {{
        max-width: 1100px;
        margin: 0 auto;
        padding: 24px;
      }}
      h1 {{
        margin: 0 0 6px 0;
        font-size: 22px;
      }}
      .sub {{
        margin: 0 0 18px 0;
        color: var(--muted);
        font-size: 13px;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin: 14px 0 18px 0;
      }}
      .card {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 12px 12px;
      }}
      .k {{
        color: var(--muted);
        font-size: 12px;
        margin-bottom: 6px;
      }}
      .v {{
        font-size: 16px;
        font-weight: 600;
      }}
      .row {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 14px;
      }}
      canvas {{
        width: 100% !important;
        height: 340px !important;
      }}
      pre {{
        background: #0e1520;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 12px;
        overflow: auto;
        color: #cfe3ff;
        font-size: 12px;
      }}
      .pill {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        font-size: 12px;
        color: var(--muted);
        margin-left: 8px;
      }}
      .pill.ok {{ color: var(--ok); border-color: rgba(64,196,99,.35); }}
      .pill.bad {{ color: var(--danger); border-color: rgba(255,107,107,.35); }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <h1>{title}</h1>
      <p class="sub">Generated at {summary.get("generated_at","")}</p>

      <div class="grid" id="cards"></div>

      <div class="row">
        <div class="panel">
          <div style="display:flex;align-items:center;gap:10px;justify-content:space-between;">
            <div>
              <div style="font-weight:600;margin-bottom:4px;">Latency over time</div>
              <div style="color:var(--muted);font-size:12px;">p50 &amp; p95 per second (ms)</div>
            </div>
            <div id="increasePill"></div>
          </div>
          <canvas id="latencyChart"></canvas>
        </div>

        <div class="panel">
          <div style="font-weight:600;margin-bottom:4px;">Throughput &amp; errors</div>
          <div style="color:var(--muted);font-size:12px;">Requests per second, errors per second</div>
          <canvas id="rpsChart"></canvas>
        </div>

        <div class="panel">
          <div style="font-weight:600;margin-bottom:10px;">Summary (JSON)</div>
          <pre id="summaryJson"></pre>
        </div>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script>
      const DATA = {payload_json};
      const series = DATA.series;

      function fmt(n) {{
        if (n === null || n === undefined) return '-';
        if (typeof n === 'number') return (Math.round(n * 10) / 10).toString();
        return String(n);
      }}

      const cardsEl = document.getElementById('cards');
      const cards = [
        ['URL', DATA.summary.url],
        ['Duration (s)', DATA.summary.duration_s],
        ['Total requests', DATA.summary.total_requests],
        ['Errors', DATA.summary.total_errors],
        ['Success rate (%)', DATA.summary.success_rate],
        ['Avg RPS', DATA.summary.avg_rps],
        ['p50 (ms)', DATA.summary.p50_ms],
        ['p95 (ms)', DATA.summary.p95_ms],
        ['p99 (ms)', DATA.summary.p99_ms],
      ];
      for (const [k,v] of cards) {{
        const div = document.createElement('div');
        div.className = 'card';
        div.innerHTML = `<div class="k">${{k}}</div><div class="v">${{fmt(v)}}</div>`;
        cardsEl.appendChild(div);
      }}

      document.getElementById('summaryJson').textContent = JSON.stringify(DATA.summary, null, 2);

      const inc = DATA.summary.increase_detected;
      const incEl = document.getElementById('increasePill');
      if (inc && inc.detected) {{
        incEl.innerHTML = `<span class="pill bad">Increase at ~${{inc.at_second}}s (p95 window=${{fmt(inc.window_p95_ms)}}ms)</span>`;
      }} else {{
        incEl.innerHTML = `<span class="pill ok">No clear increase detected</span>`;
      }}

      const labels = series.map(p => p.t_s);
      const p50 = series.map(p => p.p50_ms);
      const p95 = series.map(p => p.p95_ms);
      const rps = series.map(p => p.rps);
      const errs = series.map(p => p.errors);

      const increaseAt = (inc && inc.detected) ? inc.at_second : null;

      function markerPlugin(color, label) {{
        return {{
          id: 'marker_' + label,
          afterDraw(chart) {{
            if (increaseAt === null) return;
            const xScale = chart.scales.x;
            const yScale = chart.scales.y;
            const idx = labels.findIndex(v => v >= increaseAt);
            if (idx < 0) return;
            const x = xScale.getPixelForValue(labels[idx]);
            const ctx = chart.ctx;
            ctx.save();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.setLineDash([6,6]);
            ctx.beginPath();
            ctx.moveTo(x, yScale.top);
            ctx.lineTo(x, yScale.bottom);
            ctx.stroke();
            ctx.restore();
          }}
        }}
      }}

      new Chart(document.getElementById('latencyChart'), {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{ label: 'p50 (ms)', data: p50, borderColor: '#7aa2ff', backgroundColor: 'rgba(122,162,255,0.15)', tension: 0.2, pointRadius: 0 }},
            {{ label: 'p95 (ms)', data: p95, borderColor: '#ffb86b', backgroundColor: 'rgba(255,184,107,0.10)', tension: 0.2, pointRadius: 0 }},
          ]
        }},
        options: {{
          responsive: true,
          animation: false,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{ labels: {{ color: '#e6edf3' }} }},
            tooltip: {{ enabled: true }},
          }},
          scales: {{
            x: {{ ticks: {{ color: '#9fb0c0' }}, grid: {{ color: 'rgba(34,48,71,0.5)' }}, title: {{ display: true, text: 't (s)', color: '#9fb0c0' }} }},
            y: {{ ticks: {{ color: '#9fb0c0' }}, grid: {{ color: 'rgba(34,48,71,0.5)' }}, title: {{ display: true, text: 'ms', color: '#9fb0c0' }} }},
          }}
        }},
        plugins: [markerPlugin('#ff6b6b', 'inc')]
      }});

      new Chart(document.getElementById('rpsChart'), {{
        type: 'bar',
        data: {{
          labels,
          datasets: [
            {{ label: 'RPS', data: rps, backgroundColor: 'rgba(64,196,99,0.35)', borderColor: 'rgba(64,196,99,0.8)', borderWidth: 1 }},
            {{ label: 'Errors/s', data: errs, backgroundColor: 'rgba(255,107,107,0.35)', borderColor: 'rgba(255,107,107,0.8)', borderWidth: 1 }},
          ]
        }},
        options: {{
          responsive: true,
          animation: false,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{ labels: {{ color: '#e6edf3' }} }},
          }},
          scales: {{
            x: {{ stacked: false, ticks: {{ color: '#9fb0c0' }}, grid: {{ color: 'rgba(34,48,71,0.5)' }}, title: {{ display: true, text: 't (s)', color: '#9fb0c0' }} }},
            y: {{ stacked: false, ticks: {{ color: '#9fb0c0' }}, grid: {{ color: 'rgba(34,48,71,0.5)' }}, title: {{ display: true, text: 'count', color: '#9fb0c0' }} }},
          }}
        }},
        plugins: [markerPlugin('#ff6b6b', 'inc2')]
      }});
    </script>
  </body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Load test a webhook endpoint and generate an HTML report with when latency started increasing."
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
        "--auth-header",
        default=os.environ.get("EDA_WEBHOOK_AUTH_HEADER", "Authorization"),
        help="Auth header name (default: Authorization).",
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get("EDA_WEBHOOK_TOKEN_FILE", ""),
        help="Read auth token from a file and set it in --auth-header (avoids putting token on the CLI).",
    )
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Prompt for token (avoids putting token on the CLI).",
    )
    parser.add_argument("--template", help="JSON template file; supports ${uuid}, ${now}, ${sequence}.")
    parser.add_argument("--data", help="Inline JSON string for the request body (overrides --template).")
    parser.add_argument(
        "--vary",
        action="store_true",
        help="Vary payload per request (uuid/now/sequence placeholders).",
    )
    parser.add_argument("--warmup", type=float, default=2.0, help="Warmup seconds (not measured).")
    parser.add_argument("--duration", type=float, default=60.0, help="Measured duration in seconds.")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of worker threads.")
    parser.add_argument(
        "--rate",
        type=float,
        default=0.0,
        help="Target total requests/second. 0 = unlimited (try to saturate).",
    )
    parser.add_argument(
        "--ramp-start",
        type=float,
        default=None,
        help="Optional ramp: start total RPS (overrides --rate).",
    )
    parser.add_argument(
        "--ramp-end",
        type=float,
        default=None,
        help="Optional ramp: end total RPS by the end of --duration (overrides --rate).",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate validation.")
    parser.add_argument("--out-dir", default="reports", help="Output directory (default: reports).")
    parser.add_argument(
        "--name",
        default="",
        help="Run name (used for output filenames). Default: auto timestamp.",
    )
    parser.add_argument("--baseline-seconds", type=int, default=15, help="Baseline duration for comparison.")
    parser.add_argument("--window-seconds", type=int, default=10, help="Rolling window size for detection.")
    parser.add_argument("--increase-factor", type=float, default=1.5, help="Increase threshold vs baseline p95.")
    parser.add_argument("--increase-ms", type=float, default=0.0, help="Absolute ms increase threshold vs baseline p95.")
    parser.add_argument("--consecutive", type=int, default=3, help="Consecutive windows required.")
    args = parser.parse_args(argv)

    if not args.url:
        parser.error("Missing --url (or env EDA_WEBHOOK_URL).")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.rate < 0:
        parser.error("--rate must be >= 0")

    run_name = args.name or datetime.now().strftime("loadtest-%Y%m%d-%H%M%S")
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    samples_path = os.path.join(out_dir, f"{run_name}.samples.jsonl")
    summary_path = os.path.join(out_dir, f"{run_name}.summary.json")
    report_path = os.path.join(out_dir, f"{run_name}.report.html")

    headers = _parse_header_values(args.header)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")

    if args.token_file and args.prompt_token:
        parser.error("Use only one of --token-file or --prompt-token.")
    token = ""
    if args.token_file:
        with open(args.token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
    elif args.prompt_token:
        token = getpass.getpass(f"Token for {args.auth_header}: ").strip()
    if token:
        headers[args.auth_header] = token

    template_obj = _load_json(args.template) if args.template else None
    inline_obj = json.loads(args.data) if args.data else None

    warmup_end = time.monotonic() + max(0.0, args.warmup)
    start_measured = warmup_end
    end_measured = start_measured + max(0.0, args.duration)

    rate_mode: str | None = None
    if args.ramp_start is not None or args.ramp_end is not None:
        if args.ramp_start is None or args.ramp_end is None:
            parser.error("--ramp-start and --ramp-end must be set together.")
        if args.ramp_start < 0 or args.ramp_end < 0:
            parser.error("--ramp-start/--ramp-end must be >= 0.")

        ramp_start = float(args.ramp_start)
        ramp_end = float(args.ramp_end)
        duration = max(0.000001, float(args.duration))

        def rate_at(t_s: float) -> float:
            x = min(max(t_s / duration, 0.0), 1.0)
            return ramp_start + (ramp_end - ramp_start) * x

        limiter = RateLimiter(rate_at, start_measured)
        rate_mode = "ramp"
    elif args.rate > 0:

        def rate_at(t_s: float) -> float:
            return float(args.rate)

        limiter = RateLimiter(rate_at, start_measured)
        rate_mode = "constant"
    else:
        limiter = None
        rate_mode = None

    lock = threading.Lock()
    buckets: dict[int, Bucket] = {}
    total_requests = 0
    total_errors = 0

    stop_event = threading.Event()

    def record_sample(t_s: float, latency_ms: float | None, status: int, error: str | None) -> None:
        nonlocal total_requests, total_errors
        sec = int(t_s)
        with lock:
            b = buckets.get(sec)
            if b is None:
                b = Bucket()
                buckets[sec] = b
            b.count += 1
            total_requests += 1
            if latency_ms is not None:
                b.latencies_ms_all.append(latency_ms)
            if error is None and status >= 200 and status < 400 and latency_ms is not None:
                b.ok += 1
                b.latencies_ms_ok.append(latency_ms)
            else:
                b.errors += 1
                total_errors += 1
            key = str(status)
            b.status_counts[key] = b.status_counts.get(key, 0) + 1

    file_lock = threading.Lock()

    def write_jsonl(obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with file_lock:
            with open(samples_path, "a", encoding="utf-8") as f:
                f.write(line)

    sequence_counter = 0
    seq_lock = threading.Lock()

    def next_sequence() -> int:
        nonlocal sequence_counter
        with seq_lock:
            sequence_counter += 1
            return sequence_counter

    def worker() -> None:
        while not stop_event.is_set():
            now = time.monotonic()
            if now >= end_measured:
                return
            if limiter is not None:
                limiter.wait_turn(max(0.0, now - start_measured))
                now = time.monotonic()
                if now >= end_measured:
                    return

            seq = next_sequence()
            payload = _build_event_payload_bytes(
                template_obj=template_obj,
                inline_obj=inline_obj,
                vary=args.vary,
                sequence=seq,
            )

            t0 = time.monotonic()
            status, _, _, error = _request(
                url=args.url,
                method=args.method,
                headers=headers,
                body=payload,
                timeout_s=args.timeout,
                insecure=args.insecure,
            )
            t1 = time.monotonic()
            latency_ms = (t1 - t0) * 1000.0

            if t1 < start_measured:
                continue

            t_s = t1 - start_measured
            record_sample(t_s, latency_ms, status, error)
            write_jsonl(
                {
                    "t_s": round(t_s, 6),
                    "ts": _utc_now_iso(),
                    "latency_ms": round(latency_ms, 3),
                    "status": status,
                    "ok": error is None and 200 <= status < 400,
                    "error": error,
                }
            )

    if os.path.exists(samples_path):
        os.remove(samples_path)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    for t in threads:
        t.start()

    # Wait for measurement end.
    while time.monotonic() < end_measured:
        time.sleep(0.2)
    stop_event.set()
    for t in threads:
        t.join(timeout=5.0)

    # Build per-second series.
    with lock:
        secs = sorted(buckets.keys())
        bucket_copy = {k: buckets[k] for k in secs}
    series: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    ok_latencies: list[float] = []
    for sec in secs:
        b = bucket_copy[sec]
        lats_all = sorted(b.latencies_ms_all)
        lats_ok = sorted(b.latencies_ms_ok)
        all_latencies.extend(lats_all)
        ok_latencies.extend(lats_ok)
        p50 = _percentile(lats_all, 50)
        p95 = _percentile(lats_all, 95)
        p99 = _percentile(lats_all, 99)
        p50_ok = _percentile(lats_ok, 50)
        p95_ok = _percentile(lats_ok, 95)
        series.append(
            {
                "t_s": sec,
                "rps": b.count,
                "ok": b.ok,
                "errors": b.errors,
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
                "p50_ok_ms": p50_ok,
                "p95_ok_ms": p95_ok,
            }
        )

    all_latencies_sorted = sorted(all_latencies)
    ok_latencies_sorted = sorted(ok_latencies)
    total_duration_s = max(1.0, float(args.duration))
    avg_rps = total_requests / total_duration_s

    overall_p50 = _percentile(all_latencies_sorted, 50)
    overall_p95 = _percentile(all_latencies_sorted, 95)
    overall_p99 = _percentile(all_latencies_sorted, 99)
    overall_p50_ok = _percentile(ok_latencies_sorted, 50)
    overall_p95_ok = _percentile(ok_latencies_sorted, 95)
    overall_p99_ok = _percentile(ok_latencies_sorted, 99)

    # Detect increase start (based on rolling window p95).
    baseline_end = args.baseline_seconds
    baseline_lats: list[float] = []
    for p in series:
        if p["t_s"] < baseline_end and p["p95_ms"] is not None:
            # Use raw list to avoid approximation: pull from buckets again
            baseline_lats.extend(bucket_copy.get(int(p["t_s"]), Bucket()).latencies_ms_all)
    baseline_lats_sorted = sorted(baseline_lats)
    baseline_p95 = _percentile(baseline_lats_sorted, 95) or 0.0

    threshold = max(baseline_p95 * args.increase_factor, baseline_p95 + args.increase_ms)

    increase_info: dict[str, Any] = {
        "detected": False,
        "baseline_p95_ms": baseline_p95,
        "threshold_p95_ms": threshold,
        "at_second": None,
        "window_seconds": args.window_seconds,
        "consecutive": args.consecutive,
        "window_p95_ms": None,
    }

    consecutive = 0
    start_at: int | None = None
    start_p95: float | None = None
    if args.window_seconds > 0 and baseline_p95 > 0 and series:
        last_sec = series[-1]["t_s"]
        for window_end in range(args.window_seconds, int(last_sec) + 1):
            window_start = window_end - args.window_seconds
            window_lats: list[float] = []
            for s in range(window_start, window_end + 1):
                b = bucket_copy.get(s)
                if b:
                    window_lats.extend(b.latencies_ms_all)
            window_lats.sort()
            w_p95 = _percentile(window_lats, 95)
            if w_p95 is not None and w_p95 >= threshold:
                consecutive += 1
                if consecutive >= args.consecutive:
                    start_at = window_start
                    start_p95 = w_p95
                    break
            else:
                consecutive = 0

    if start_at is not None:
        increase_info["detected"] = True
        increase_info["at_second"] = start_at
        increase_info["window_p95_ms"] = start_p95

    summary = {
        "generated_at": _utc_now_iso(),
        "url": args.url,
        "method": args.method,
        "duration_s": args.duration,
        "warmup_s": args.warmup,
        "concurrency": args.concurrency,
        "rate_mode": rate_mode,
        "target_rps": args.rate if (rate_mode == "constant") else None,
        "ramp_rps": {"start": args.ramp_start, "end": args.ramp_end} if (rate_mode == "ramp") else None,
        "timeout_s": args.timeout,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "success_rate": round((0.0 if total_requests == 0 else (1.0 - (total_errors / total_requests))) * 100.0, 2),
        "avg_rps": round(avg_rps, 2),
        "p50_ms": overall_p50,
        "p95_ms": overall_p95,
        "p99_ms": overall_p99,
        "p50_ok_ms": overall_p50_ok,
        "p95_ok_ms": overall_p95_ok,
        "p99_ok_ms": overall_p99_ok,
        "increase_detected": increase_info,
        "artifacts": {
            "samples_jsonl": samples_path,
            "summary_json": summary_path,
            "report_html": report_path,
        },
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)

    _write_html_report(
        out_path=report_path,
        title=f"Load test report: {run_name}",
        summary=summary,
        series=[
            {
                "t_s": p["t_s"],
                "p50_ms": p["p50_ms"],
                "p95_ms": p["p95_ms"],
                "rps": p["rps"],
                "errors": p["errors"],
            }
            for p in series
        ],
    )

    print(_json_dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
