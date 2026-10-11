"""Live feed for the Live tab: in memory only, pauses during experiments, never touches the database."""
import time
import unittest
from types import SimpleNamespace

from dashboard import livefeed


def probe(name):
    return {"ok": name != "cart", "latency_ms": 12.5, "status": 200 if name != "cart" else 500}


probe.names = ["browse", "cart", "checkout"]


class LiveFeedTest(unittest.TestCase):
    def test_a_tick_appends_one_row_per_journey_in_the_samples_shape(self):
        feed = livefeed.LiveFeed(probe=probe, busy=lambda slot: False)
        feed.tick()
        rows = feed.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), len(livefeed.COLUMNS))
        by_name = {r[2]: r for r in rows}
        self.assertEqual((by_name["browse"][3], by_name["cart"][3]), (1, 0))
        self.assertIsNone(rows[0][1])          # no experiment id: scoring can never read these

    def test_it_pauses_while_an_experiment_is_active(self):
        feed = livefeed.LiveFeed(probe=probe, busy=lambda slot: True)
        feed.tick()
        self.assertEqual(feed.rows(), [])
        self.assertTrue(feed.paused)

    def test_old_rows_are_dropped(self):
        feed = livefeed.LiveFeed(probe=probe, busy=lambda slot: False, keep_s=0)
        feed.tick()
        time.sleep(0.01)
        feed.tick()
        self.assertEqual(len(feed.rows()), 3)  # only the latest round survives a zero window

    def test_thread_starts_and_stops_and_survives_probe_errors(self):
        def boom(name):
            raise RuntimeError("shop down")
        boom.names = ["browse"]
        feed = livefeed.LiveFeed(interval_s=0.5, probe=boom, busy=lambda slot: False)
        feed.start()
        time.sleep(0.3)
        self.assertTrue(feed.running())
        self.assertIn("shop down", feed.last_error or "")
        feed.stop()
        feed.thread.join(3)
        self.assertFalse(feed.running())

    def test_experiment_active_when_the_dashboard_run_is_alive(self):
        self.assertTrue(livefeed.experiment_active(SimpleNamespace(alive=lambda: True)))
        recent = lambda: SimpleNamespace(execute=lambda q: SimpleNamespace(fetchone=lambda: (time.time(),)), close=lambda: None)  # noqa: E731
        self.assertTrue(livefeed.experiment_active(None, db_connect=recent))
        old = lambda: SimpleNamespace(execute=lambda q: SimpleNamespace(fetchone=lambda: (time.time() - 600,)), close=lambda: None)  # noqa: E731
        self.assertFalse(livefeed.experiment_active(None, db_connect=old))


if __name__ == "__main__":
    unittest.main()
