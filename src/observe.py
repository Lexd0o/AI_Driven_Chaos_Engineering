"""Sampler + SLI helpers over SQLite. P2 (Observability).

Table `samples` (one row per probe result):
  ts REAL (UTC epoch s, VM clock) | experiment_id TEXT NULL | journey TEXT | ok INT | latency_ms REAL | status INT
  service TEXT | metric TEXT | value REAL      <- per-service rows (added later); NULL for journey rows

Table `runs` (one row per chaos run, Architecture.md 4.4; written by src/chaos/controller.py via save_run):
  see RUN_COLUMNS below. experiment_id = the chaos run id (D-025).

Run alone:  python -m src.observe --seconds 30 [--experiment EXP_ID]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from src.probe import run_journey
from src.telemetry import ROOT, load_config, prom_query

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  ts REAL NOT NULL, experiment_id TEXT, journey TEXT, ok INTEGER, latency_ms REAL, status INTEGER,
  service TEXT, metric TEXT, value REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_j_ts ON samples(journey, ts);
CREATE INDEX IF NOT EXISTS idx_samples_exp ON samples(experiment_id);
CREATE TABLE IF NOT EXISTS runs (
  experiment_id TEXT PRIMARY KEY, repeat INTEGER, mode TEXT, target TEXT, fault_type TEXT,
  t_start REAL, t_inject REAL, t_breach REAL, t_detect REAL, t_clear REAL, t_healthy REAL,
  aborted INTEGER, abort_reason TEXT,
  outcome TEXT, quality TEXT,
  peak_error_rate REAL, peak_p95_ms REAL, impact_recovery_s REAL, post_fault_recovery_s REAL, containment_C REAL,
  forced_breach INTEGER,
  journeys TEXT, evidence TEXT, report_path TEXT
);
"""

RUN_COLUMNS = [
    "experiment_id", "repeat", "mode", "target", "fault_type",
    "t_start", "t_inject", "t_breach", "t_detect", "t_clear", "t_healthy",
    "aborted", "abort_reason", "outcome", "quality",
    "peak_error_rate", "peak_p95_ms", "impact_recovery_s", "post_fault_recovery_s", "containment_C",
    "forced_breach", "journeys", "evidence", "report_path",
]
RUN_JSON_COLUMNS = ("journeys", "evidence")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else ROOT / load_config()["db_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # readers (P4 guardrail, dashboard) don't block the writer
    con.executescript(SCHEMA)
    return con


def sample_services(cfg: dict, ts: float, experiment_id: str | None) -> list[tuple]:
    """One row per (service, metric) from Prometheus. Returns [] if Prometheus is unreachable (never raises)."""
    rows: list[tuple] = []
    try:
        for metric, promql in cfg.get("service_queries", {}).items():
            for series in prom_query(promql):
                svc = series["metric"].get("service_name")
                val = float(series["value"][1])
                if svc and val == val:  # skip NaN (e.g. p95 with no traffic)
                    rows.append((ts, experiment_id, None, None, None, None, svc, metric, val))
    except Exception:  # noqa: BLE001 - journey sampling must keep running
        return []
    return rows


def run_sampler(experiment_id: str | None = None, stop_after_s: int | None = None,
                db_path: str | None = None, stop_event: threading.Event | None = None) -> None:
    """Probe every journey each interval (in parallel) and write rows.

    Stops on Ctrl+C, after stop_after_s seconds, or when stop_event is set (used when the sampler runs in a
    background thread, where Ctrl+C never reaches it)."""
    cfg = load_config()
    interval = cfg.get("sample_interval_s", 2)
    names = list(cfg["journeys"])
    con = connect(db_path)
    start = time.time()
    last_svc = 0.0
    try:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            while stop_after_s is None or time.time() - start < stop_after_s:
                if stop_event is not None and stop_event.is_set():
                    break
                tick = time.time()
                results = list(pool.map(lambda n: (n, run_journey(n, cfg)), names))
                rows = [(tick, experiment_id, n, int(r["ok"]), r["latency_ms"], r["status"],
                         None, None, None) for n, r in results]
                if tick - last_svc >= cfg.get("service_sample_interval_s", 10):
                    rows += sample_services(cfg, tick, experiment_id)
                    last_svc = tick
                con.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)", rows)
                con.commit()
                pause = max(0.0, interval - (time.time() - tick))
                if stop_event is not None:
                    stop_event.wait(pause)   # wakes at once when stop_event is set
                else:
                    time.sleep(pause)
    except KeyboardInterrupt:
        pass
    finally:
        con.close()


def read_samples(experiment_id: str, db_path: str | None = None) -> list[dict]:
    con = connect(db_path)
    rows = con.execute("SELECT * FROM samples WHERE experiment_id=? ORDER BY ts", (experiment_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---- runs table (Architecture.md 4.4) ----
def save_run(record: dict, db_path: str | None = None) -> None:
    """Insert or replace one run row. Keys = RUN_COLUMNS (missing -> NULL). dict/list values are stored as JSON text."""
    values = []
    for col in RUN_COLUMNS:
        v = record.get(col)
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        elif isinstance(v, bool):
            v = int(v)
        values.append(v)
    con = connect(db_path)
    try:
        con.execute(f"INSERT OR REPLACE INTO runs ({','.join(RUN_COLUMNS)}) "
                    f"VALUES ({','.join('?' * len(RUN_COLUMNS))})", values)
        con.commit()
    finally:
        con.close()


def read_runs(db_path: str | None = None) -> list[dict]:
    """All run rows, oldest first; the JSON columns (journeys, evidence) come back as dicts."""
    con = connect(db_path)
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY t_start")]
    finally:
        con.close()
    for r in rows:
        for col in RUN_JSON_COLUMNS:
            if r.get(col):
                r[col] = json.loads(r[col])
    return rows


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"success_rate": None, "p50_ms": None, "p95_ms": None, "n": 0}
    lat = [r["latency_ms"] for r in rows if r["ok"]]  # latency of successful requests only
    return {
        "success_rate": sum(r["ok"] for r in rows) / n,
        "p50_ms": float(np.percentile(lat, 50)) if lat else None,
        "p95_ms": float(np.percentile(lat, 95)) if lat else None,
        "n": n,
    }


def latest_sli(journey: str, window_s: int = 10, db_path: str | None = None) -> dict:
    """Success rate / p50 / p95 over the last window_s seconds. n==0 means NO DATA (not healthy)."""
    con = connect(db_path)
    rows = con.execute(
        "SELECT ok, latency_ms FROM samples WHERE journey=? AND ts>=? ORDER BY ts",
        (journey, time.time() - window_s)).fetchall()
    con.close()
    return _stats([dict(r) for r in rows])


def zscore(value: float, mean: float, std: float, min_std: float) -> float:
    """min_std stops near-zero baseline variance from producing absurd scores."""
    return (value - mean) / max(std, min_std)


# ---- breach / recovery detection (pure functions on a list of sample dicts) ----
# slo = {"journey": "checkout", "min_success": 0.9, "max_p95_ms": 1500, "window_s": 10, "min_n": 3}
def _window_unhealthy(rows: list[dict], i: int, slo: dict) -> bool | None:
    """Is the rolling window ending at rows[i] unhealthy? None = too few points to judge."""
    win_s = slo.get("window_s", 10)
    t = rows[i]["ts"]
    window = [r for r in rows[: i + 1] if r["ts"] > t - win_s]
    if len(window) < slo.get("min_n", 3):
        return None
    st = _stats(window)
    if st["success_rate"] < slo["min_success"]:
        return True
    p95 = st["p95_ms"]
    return p95 is not None and p95 > slo["max_p95_ms"]


def _journey_rows(samples: list[dict], slo: dict) -> list[dict]:
    return sorted((s for s in samples if s.get("journey") == slo["journey"]), key=lambda r: r["ts"])


def find_breach(samples: list[dict], slo: dict) -> float | None:
    """Timestamp of the first sample whose rolling window violates the SLO, else None."""
    rows = _journey_rows(samples, slo)
    for i in range(len(rows)):
        if _window_unhealthy(rows, i, slo):
            return rows[i]["ts"]
    return None


def find_healthy(samples: list[dict], slo: dict, after_ts: float, sustain_s: int = 30) -> float | None:
    """Start of the first window after after_ts that stays healthy for >= sustain_s, else None."""
    rows = _journey_rows(samples, slo)
    streak_start = None
    for i, r in enumerate(rows):
        if r["ts"] < after_ts:
            continue
        bad = _window_unhealthy(rows, i, slo)
        if bad is None or bad:
            streak_start = None
            continue
        if streak_start is None:
            streak_start = r["ts"]
        if r["ts"] - streak_start >= sustain_s:
            return streak_start
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=None)
    ap.add_argument("--experiment", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    print("Sampling... Ctrl+C to stop")
    run_sampler(a.experiment, a.seconds, a.db)
    for j in load_config()["journeys"]:
        print(j, latest_sli(j, 3600, a.db))
