"""Dry run of the whole P2 chain on a FAKE shop (rehearses the G3 breach test). SYNTHETIC DATA ONLY.

    python -m tools.dry_run                 (repo folder, venv active, ~2 min)
    python -m tools.dry_run --fault-s 20 --post-s 40 --budget 60

What it does, in order:
  1. starts the fake shop on a free local port (no Docker, no real shop touched)
  2. runs the sampler in the background, tagged with experiment id "dryrun-1"
  3. waits --pre-s, then makes checkout fail 100% for --fault-s (this is t_inject .. t_clear)
  4. clears the fault, keeps sampling for --post-s
  5. prints collect_run_metrics() and collect_evidence() and saves them to data/dry_run_report.json

Everything goes to data/dry_run.db (git-ignored). Numbers from this run are NOT results: never report or commit them.
View it in the dashboard (PowerShell):
    $env:DASHBOARD_DB="data/dry_run.db"; streamlit run dashboard/app.py
"""
import argparse
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

from tools import fake_shop

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "dry_run.db"
REPORT = ROOT / "data" / "dry_run_report.json"
EXPERIMENT = "dryrun-1"


def set_fake_fault(base_url, checkout):
    """Change the fake shop's checkout failure rate (0.0 - 1.0)."""
    requests.get(f"{base_url}/_fail", params={"checkout": checkout}, timeout=3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre-s", type=int, default=20, help="healthy seconds before the fault")
    ap.add_argument("--fault-s", type=int, default=30, help="how long checkout fails")
    ap.add_argument("--post-s", type=int, default=60, help="seconds watched after the fault is cleared")
    ap.add_argument("--budget", type=int, default=60, help="recovery budget in seconds")
    ap.add_argument("--port", type=int, default=0, help="fake shop port (0 = pick a free one)")
    a = ap.parse_args()

    # 1. fake shop, in this process
    server = ThreadingHTTPServer(("127.0.0.1", a.port), fake_shop.H)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Point every P2 module at the fake shop. Dead ports for Prometheus/Jaeger: this run must never mix
    # with real data (evidence will say "unreachable", which also tests that it never raises).
    os.environ["PROBE_BASE_URL"] = base_url
    os.environ["PROMETHEUS_URL"] = "http://127.0.0.1:9"
    os.environ["JAEGER_URL"] = "http://127.0.0.1:9"

    # import after the env vars are set
    from src.evidence import collect_evidence
    from src.observe import run_sampler
    from src.runmetrics import collect_run_metrics, make_slos

    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)
    DB.parent.mkdir(parents=True, exist_ok=True)

    total_s = a.pre_s + a.fault_s + a.post_s
    print(f"[SYNTHETIC] fake shop at {base_url}; total about {total_s}s")

    # 2. sampler in the background
    sampler = threading.Thread(target=run_sampler, args=(EXPERIMENT, total_s, str(DB)), daemon=True)
    sampler.start()

    # 3. fault window
    time.sleep(a.pre_s)
    t_inject = time.time()
    set_fake_fault(base_url, 1.0)
    print("fault ON: checkout fails 100%")
    time.sleep(a.fault_s)
    set_fake_fault(base_url, 0.0)
    t_clear = time.time()
    print("fault OFF")

    # 4. recovery
    sampler.join()
    server.shutdown()

    # 5. numbers
    slos = make_slos()
    metrics = collect_run_metrics(EXPERIMENT, ["checkout"], t_inject, t_clear, slos=slos,
                                  recovery_budget_s=a.budget, db_path=str(DB))
    evidence = collect_evidence(EXPERIMENT, t_inject - 5, t_clear + a.post_s)
    out = {"SYNTHETIC": True, "metrics": metrics, "evidence": evidence}
    REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n=== run metrics (SYNTHETIC) ===")
    print(json.dumps(metrics, indent=2))
    print("\nevidence quality:", evidence["quality"], "| notes:", [n[:70] for n in evidence["notes"][:2]])
    print("(partial is expected here: Prometheus/Jaeger are deliberately pointed at a dead port)")
    print(f"\nSaved {REPORT}. Expected: outcome degraded_recovered, checkout A near 0, browse/cart contained (C = 1.0).")


if __name__ == "__main__":
    main()
