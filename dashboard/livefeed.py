"""In-memory live probe feed for the Live tab graphs.

Probes the shop's journeys every few seconds and keeps the last ~20 minutes in memory only. It never writes to the
samples database, so it cannot trigger the controller's "another sampler is writing" refusal, and it never touches
scoring or saved runs. It pauses while an experiment is running or has written samples in the last 15 s."""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

COLUMNS = ["ts", "experiment_id", "journey", "ok", "latency_ms", "status", "service", "metric", "value"]


class LiveFeed:
    def __init__(self, interval_s: float = 3.0, keep_s: int = 1200, probe=None, busy=None):
        self.interval_s, self.keep_s = interval_s, keep_s
        self._probe, self._busy = probe, busy      # injectable for tests
        self._rows: deque = deque()
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_error = None
        self.paused = False

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def rows(self) -> list[tuple]:
        with self._lock:
            return list(self._rows)

    def tick(self, slot=None) -> None:
        """One probe round for every journey (in parallel); appends rows unless an experiment is active."""
        busy = self._busy(slot) if self._busy else False
        self.paused = bool(busy)
        if busy:
            return
        probe = self._probe
        if probe is None:
            from src.probe import run_journey
            from src.telemetry import load_config
            cfg = load_config()
            names = list(cfg["journeys"])
            probe = lambda n: run_journey(n, cfg)   # noqa: E731
        else:
            names = list(getattr(probe, "names", ["browse", "cart", "checkout"]))
        now = time.time()
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            results = list(pool.map(lambda n: (n, probe(n)), names))
        with self._lock:
            for n, r in results:
                self._rows.append((now, None, n, int(bool(r["ok"])), r["latency_ms"], r["status"], None, None, None))
            while self._rows and self._rows[0][0] < now - self.keep_s:
                self._rows.popleft()
        self.last_error = None

    def _loop(self, slot) -> None:
        while not self.stop_event.is_set():
            started = time.time()
            try:
                self.tick(slot)
            except Exception as error:  # noqa: BLE001 - a missing shop must not kill the thread
                self.last_error = f"{type(error).__name__}: {error}"
            self.stop_event.wait(max(0.5, self.interval_s - (time.time() - started)))

    def start(self, slot=None) -> None:
        if self.running():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, args=(slot,), daemon=True, name="live-feed")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()


def experiment_active(slot, within_s: int = 15, db_connect=None) -> bool:
    """True while the dashboard's own run is alive, or any experiment wrote samples in the last within_s seconds."""
    if slot is not None and slot.alive():
        return True
    try:
        if db_connect is None:
            from src.observe import connect as db_connect
        con = db_connect()
        try:
            row = con.execute("SELECT MAX(ts) FROM samples WHERE experiment_id IS NOT NULL").fetchone()
        finally:
            con.close()
        return bool(row and row[0] and time.time() - row[0] < within_s)
    except Exception:  # noqa: BLE001
        return False
