"""Batch runner: order, halting, STOP file, status file, and the dashboard's launch of it. No cluster is touched."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dashboard import control
from src import run_all


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.status = str(Path(self.tmp.name) / "status.json")
        self.stop = str(Path(self.tmp.name) / "STOP")
        self.calls = []

    def runner(self, fail_on=None, create_stop_after=None):
        def run(argv, **kw):
            self.calls.append(argv[argv.index("-m") + 1:] if "-m" in argv else argv)
            code = 1 if fail_on and fail_on in argv else 0
            if create_stop_after and create_stop_after in argv:
                Path(self.stop).write_text("x")
            return SimpleNamespace(returncode=code)
        return run

    def test_runs_baseline_then_experiment_for_each_then_scores(self):
        state = run_all.run_batch(["exp-002", "exp-008"], self.stop, self.status, 0, runner=self.runner(), sleep=lambda s: None)
        self.assertEqual(state, "done")
        modules = [c[0] for c in self.calls]
        self.assertEqual(modules, ["src.chaos.k8s_monitor", "src.run", "src.chaos.k8s_monitor", "src.run", "src.score", "src.report"])
        self.assertIn("--yes", self.calls[1])
        self.assertEqual([i["status"] for i in json.loads(Path(self.status).read_text())["items"]], ["ok", "ok"])

    def test_halts_at_the_first_failure_and_still_scores(self):
        state = run_all.run_batch(["exp-002", "exp-008"], self.stop, self.status, 0, runner=self.runner(fail_on="exp-002"),
                                  sleep=lambda s: None)
        self.assertEqual(state, "failed")
        items = json.loads(Path(self.status).read_text())["items"]
        self.assertEqual([i["status"] for i in items], ["failed", "skipped"])
        self.assertNotIn(["src.run", "-u"], [c[:2] for c in self.calls if "exp-008" in c])

    def test_keep_going_continues_after_a_failure(self):
        run_all.run_batch(["exp-002", "exp-008"], self.stop, self.status, 0, keep_going=True,
                          runner=self.runner(fail_on="exp-002"), sleep=lambda s: None)
        self.assertEqual([i["status"] for i in json.loads(Path(self.status).read_text())["items"]], ["failed", "ok"])

    def test_a_stop_file_halts_before_the_next_experiment(self):
        state = run_all.run_batch(["exp-002", "exp-008"], self.stop, self.status, 0,
                                  runner=self.runner(create_stop_after="exp-002"), sleep=lambda s: None)
        self.assertEqual(state, "halted")
        self.assertEqual([i["status"] for i in json.loads(Path(self.status).read_text())["items"]], ["ok", "skipped"])

    def test_existing_stop_file_runs_nothing(self):
        Path(self.stop).write_text("x")
        state = run_all.run_batch(["exp-002"], self.stop, self.status, 0, runner=self.runner(), sleep=lambda s: None)
        self.assertEqual(state, "halted")
        self.assertEqual(self.calls, [])


class DashboardBatchTest(unittest.TestCase):
    PLAN = [{"experiment_id": "exp-002"}, {"experiment_id": "exp-003"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [mock.patch.object(control, "RUN_LOG", base / "run.log"),
                        mock.patch.object(control, "ACTIONS_LOG", base / "actions.log")]
        for p in self.patches:
            p.start()
        self.stop = base / "STOP"
        self.slot = control.RunSlot()

    def tearDown(self):
        self.slot.close_log()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def start(self, ids=("exp-002", "exp-003"), typed=control.CONFIRM_RUN, why=None):
        popen = mock.MagicMock(side_effect=lambda argv, **kw: SimpleNamespace(
            argv=argv, poll=lambda: None, stdin=SimpleNamespace(write=lambda t: setattr(self, "written", t), close=lambda: None)))
        self.popen = popen
        return control.start_batch(self.slot, list(ids), typed, self.PLAN, lambda item: why, stop_path=self.stop, popen=popen)

    def test_wrong_word_unknown_id_refusal_stop_and_empty_start_nothing(self):
        for kwargs in ({"typed": "yes"}, {"ids": ["exp-999"]}, {"why": "refused"}, {"ids": []}):
            self.assertFalse(self.start(**kwargs)[0])
        self.stop.write_text("x")
        self.assertFalse(self.start()[0])

    def test_start_launches_run_all_and_answers_the_prompt(self):
        ok, _ = self.start()
        self.assertTrue(ok)
        argv = self.slot.proc.argv
        self.assertEqual(argv[1:6], ["-u", "-m", "src.run_all", "--exp-ids", "exp-002"])
        self.assertEqual(self.written, control.CONFIRM_RUN + "\n")
        self.assertFalse(self.start()[0])   # one at a time


if __name__ == "__main__":
    unittest.main()
