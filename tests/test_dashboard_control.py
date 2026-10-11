"""Dashboard Control tab logic: starting and recalling runs, validating and applying fixes. No cluster is touched."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dashboard import control

PLAN = [{"experiment_id": "exp-004", "rank": 4, "fault_type": "pod_kill", "risk_id": "R-cart-single_replica_no_pdb",
         "target": {"service": "cart"}, "hypothesis": "h", "reason": "r", "params": {},
         "expected_impact": {"journeys": ["cart"]}}]
CART_PATCH = "kubectl -n otel-demo patch deployment cart --patch-file reports/fixes/R-cart-single_replica_no_pdb.yaml"
CART_PDB = "kubectl apply -f reports/fixes/R-cart-single_replica_no_pdb.pdb.yaml"


class FakeProc:
    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs, self.written = argv, kwargs, ""
        self.stdin = SimpleNamespace(write=self._write, close=lambda: None)
        self._rc = None

    def _write(self, text):
        self.written += text

    def poll(self):
        return self._rc


class InversePatchTest(unittest.TestCase):
    def test_replicas_are_restored(self):
        self.assertEqual(control.inverse_patch({"spec": {"replicas": 1, "x": 1}}, {"spec": {"replicas": 2}}),
                         {"spec": {"replicas": 1}})

    def test_a_probe_that_did_not_exist_is_deleted_with_null(self):
        original = {"spec": {"template": {"spec": {"containers": [{"name": "cart", "image": "i"}]}}}}
        patch = {"spec": {"template": {"spec": {"containers": [{"name": "cart", "livenessProbe": {"grpc": {"port": 8080}}}]}}}}
        undo = control.inverse_patch(original, patch)
        self.assertEqual(undo["spec"]["template"]["spec"]["containers"], [{"name": "cart", "livenessProbe": None}])

    def test_an_existing_probe_value_is_put_back(self):
        original = {"spec": {"template": {"spec": {"containers": [
            {"name": "cart", "livenessProbe": {"periodSeconds": 10, "timeoutSeconds": 1}}]}}}}
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": "cart", "livenessProbe": {"periodSeconds": 20}}]}}}}
        undo = control.inverse_patch(original, patch)
        self.assertEqual(undo["spec"]["template"]["spec"]["containers"][0]["livenessProbe"], {"periodSeconds": 10})


class ParseApplyTest(unittest.TestCase):
    def test_accepts_the_generated_commands(self):
        patch = control.parse_apply(CART_PATCH)
        self.assertEqual((patch["verb"], patch["service"]), ("patch", "cart"))
        self.assertEqual(control.parse_apply(CART_PDB)["verb"], "apply")

    def test_rejects_anything_else(self):
        bad = ["kubectl delete -f reports/fixes/R-cart-single_replica_no_pdb.pdb.yaml",
               "rm -rf /",
               CART_PATCH.replace("otel-demo", "kube-system"),
               CART_PATCH + " && kubectl delete ns otel-demo",
               "kubectl apply -f ../../etc/passwd.yaml",
               "kubectl apply -f reports/fixes/does-not-exist.yaml",
               "kubectl apply -f config/policy.yaml"]
        for cmd in bad:
            with self.assertRaises(ValueError, msg=cmd):
                control.parse_apply(cmd)

    def test_every_command_in_the_real_index_is_accepted_or_refused_cleanly(self):
        for fix in control.load_fixes():
            for f in fix.get("files") or []:
                control.parse_apply(f["apply"])   # must not raise


class RunControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [mock.patch.object(control, "RUN_LOG", base / "run.log"),
                        mock.patch.object(control, "ACTIONS_LOG", base / "actions.log")]
        for p in self.patches:
            p.start()
        self.stop = base / "STOP"
        self.slot = control.RunSlot()
        self.popen = mock.MagicMock(side_effect=FakeProc)

    def tearDown(self):
        self.slot.close_log()   # Windows cannot delete a file that is still open
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def start(self, typed=control.CONFIRM_RUN, exp="exp-004", why=None):
        return control.start_run(self.slot, exp, typed, PLAN, lambda item: why, stop_path=self.stop, popen=self.popen)

    def test_wrong_word_starts_nothing(self):
        ok, _ = self.start(typed="yes")
        self.assertFalse(ok)
        self.popen.assert_not_called()

    def test_unknown_experiment_and_refusals_start_nothing(self):
        self.assertFalse(self.start(exp="exp-999")[0])
        ok, message = self.start(why="carries the probe port-forward")
        self.assertFalse(ok)
        self.assertIn("probe port-forward", message)
        self.popen.assert_not_called()

    def test_stop_file_blocks_a_run(self):
        self.stop.write_text("x")
        ok, message = self.start()
        self.assertFalse(ok)
        self.assertIn("STOP", message)
        self.popen.assert_not_called()

    def test_start_runs_the_real_command_and_answers_the_prompt(self):
        ok, _ = self.start()
        self.assertTrue(ok)
        proc = self.slot.proc
        self.assertEqual(proc.argv[1:], ["-u", "-m", "src.run", "--exp", "exp-004", "--execute"])
        self.assertEqual(proc.written, control.CONFIRM_RUN + "\n")
        self.assertNotIn("--yes", proc.argv)

    def test_only_one_run_at_a_time(self):
        self.assertTrue(self.start()[0])
        ok, message = self.start()
        self.assertFalse(ok)
        self.assertIn("still running", message)
        self.slot.proc._rc = 0
        self.assertTrue(self.start()[0])

    def test_recall_runs_stop_all_and_rearm_deletes_the_file(self):
        runner = mock.MagicMock(return_value=SimpleNamespace(returncode=0, stdout="deleted", stderr=""))
        ok, out = control.recall(python="py", runner=runner)
        self.assertTrue(ok)
        self.assertEqual(runner.call_args.args[0], ["py", "-m", "src.run", "--stop-all"])
        self.stop.write_text("x")
        self.assertTrue(control.rearm(self.stop))
        self.assertFalse(self.stop.exists())
        self.assertFalse(control.rearm(self.stop))

    def test_stage_and_guardian_text(self):
        log = "STAGE: using_plan_item\n\nSTAGE: pre_observation\n\nSTAGE: guarding\n"
        self.assertEqual(control.current_stage(log), "guarding")
        self.assertTrue(control.guardian_state(log, finished=False).startswith("ARMED"))
        self.assertTrue(control.guardian_state("STAGE: pre_observation", finished=False).startswith("Not armed"))
        self.assertTrue(control.guardian_state("Guardian ended with interrupted.", finished=True).startswith("TRIGGERED"))


class FixControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [mock.patch.object(control, "BACKUP_DIR", base / "bk"),
                        mock.patch.object(control, "REGISTRY", base / "bk" / "registry.json"),
                        mock.patch.object(control, "ACTIONS_LOG", base / "actions.log")]
        for p in self.patches:
            p.start()
        self.calls = []
        self.deployment = {"spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "cart"}]}}}}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        out = json.dumps(self.deployment) if "get" in argv else "ok"
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    def test_wrong_word_or_running_experiment_applies_nothing(self):
        parsed = control.parse_apply(CART_PATCH)
        self.assertFalse(control.apply_fix(parsed, "yes", None, self.runner)[0])
        busy = control.RunSlot()
        busy.proc = FakeProc([])
        self.assertFalse(control.apply_fix(parsed, control.CONFIRM_FIX, busy, self.runner)[0])
        self.assertEqual(self.calls, [])

    def test_dry_run_adds_the_server_flag_and_changes_nothing(self):
        control.dry_run_fix(control.parse_apply(CART_PATCH), self.runner)
        self.assertEqual(self.calls[0][-1], "--dry-run=server")
        self.assertEqual(self.calls[0][1:4], ["-n", "otel-demo", "patch"])

    def test_apply_saves_the_undo_then_rollback_restores_replicas(self):
        parsed = control.parse_apply(CART_PATCH)
        ok, _ = control.apply_fix(parsed, control.CONFIRM_FIX, None, self.runner)
        self.assertTrue(ok)
        self.assertTrue(control.is_applied(parsed))
        undo = json.loads(Path(control._registry()[parsed["file"].name]["undo"]).read_text())
        self.assertEqual(undo, {"spec": {"replicas": 1}})
        self.assertIn("get", self.calls[0])
        ok, _ = control.rollback_fix(parsed, control.CONFIRM_FIX, None, self.runner)
        self.assertTrue(ok)
        self.assertEqual(self.calls[-1][-2], "--patch-file")
        self.assertFalse(control.is_applied(parsed))

    def test_new_objects_roll_back_by_deleting_them(self):
        parsed = control.parse_apply(CART_PDB)
        self.assertTrue(control.apply_fix(parsed, control.CONFIRM_FIX, None, self.runner)[0])
        self.assertTrue(control.rollback_fix(parsed, control.CONFIRM_FIX, None, self.runner)[0])
        self.assertEqual(self.calls[-1][:3], ["kubectl", "delete", "-f"])

    def test_cannot_roll_back_what_the_dashboard_did_not_apply(self):
        ok, message = control.rollback_fix(control.parse_apply(CART_PATCH), control.CONFIRM_FIX, None, self.runner)
        self.assertFalse(ok)
        self.assertIn("nothing saved", message)


class PlanRowsTest(unittest.TestCase):
    def test_rows_show_runnable_and_latest_result(self):
        scores = {"experiments": [{"experiment_id": "exp-004", "S": 32.66, "severity": "High", "status": "scored",
                                   "runs": [{"outcome": "degraded_recovered"}]}]}
        rows = control.plan_rows(PLAN, scores, lambda item: None)
        self.assertEqual((rows[0]["can run"], rows[0]["S"], rows[0]["severity"]), ("yes", 32.7, "High"))
        refused = control.plan_rows(PLAN, None, lambda item: "carries the probe port-forward")
        self.assertTrue(refused[0]["can run"].startswith("no:"))
        self.assertEqual(refused[0]["result"], "not run")


class ControlTabSmokeTest(unittest.TestCase):
    def test_the_tab_renders_without_errors(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            self.skipTest("streamlit not installed")
        at = AppTest.from_file(str(control.ROOT / "dashboard" / "app.py"), default_timeout=30).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        labels = [b.label for b in at.button]
        self.assertTrue(any("Start experiment" in label for label in labels))
        self.assertTrue(any("Recall" in label for label in labels))
        start = [b for b in at.button if "Start experiment" in b.label][0]
        self.assertTrue(start.proto.disabled)   # nothing typed and no fresh guard baseline: it cannot start


if __name__ == "__main__":
    unittest.main()
