# Chaos agent (OpenTelemetry Demo, Chaos Mesh)

Plans, injects and safely stops one chaos experiment against the demo
running on the `docker-desktop` cluster (namespace `otel-demo`).

## Files

| File | Purpose |
|---|---|
| `app_mapper.py` | Builds the service map from Jaeger dependencies (last 1h) and kubectl workloads. Writes `app_map.json`, `app_map.md` (Mermaid) and regenerates `service_snapshot.json` for the planner. |
| `app_map_overrides.json` | Manual corrections: `add_edges`, `remove_edges` (`[parent, child]` pairs) and `notes`. |
| `monitor.py` | Prometheus signals, synthetic probe, pod health, baseline capture. |
| `guardian.py` | Safety loop, kill switch and recovery measurement (no LLM). |
| `chaos_planner.py` | Groq LLM produces a validated plan in `experiment.json`. |
| `chaos_executor.py` | Builds the Chaos Mesh manifest. Modes `--mock`, `--preview`, `--execute`. |
| `controller.py` | Pipeline: `--mock` (default) or `--live`. Writes `reports/<run_id>.json` and `.md`. |
| `settings.json` | Context, namespace, approved targets, safety thresholds. |
| `tests/test_guardian.py` | Offline guardian tests (fakes, no kubectl). |

## Day-1 milestones

- Application mapping: `app_mapper.py`
- Monitoring baseline: `monitor.py --baseline 60` (writes `baseline.json`)
- First failure injection: `controller.py --live`
- Safety controls: `guardian.py` plus the `safety` block in `settings.json`
- Day-1 checkpoint: a `reports/<run_id>.md` from a live run (and a forced-breach run)

## Setup

```
pip install -r requirements.txt
```

Put `GROQ_API_KEY` in `.env` (only needed when the planner runs). `kubectl`
must be on PATH. Prometheus, Jaeger and the storefront are reached through the
API-server proxy, so no port-forward is needed. Prometheus scrapes every
minute, so rates use a 2m window.

## Commands

```
python app_mapper.py                      # map + snapshot
python monitor.py --baseline 60           # baseline.json
python chaos_executor.py --preview        # server-side dry run
python controller.py --mock               # unchanged mock pipeline (calls Groq)
python controller.py --live               # map, plan (Groq), preview, baseline, preflight, confirm, inject, guard
python controller.py --live --skip-plan   # same, using the existing experiment.json (no Groq)
python controller.py --live --skip-plan --force-breach   # safety demo
python controller.py --live --yes         # skip the typed confirmation
python -m unittest discover -s tests      # offline tests
```

`--live --force-breach` still injects the planned fault. It only sets
`latency_floor_ms=1` and `latency_multiplier=0` so the guardian trips and
stops it automatically. `--baseline-seconds N` changes the baseline length.

Live pre-flight refuses to inject if the target is not fully ready, any
podchaos/networkchaos exists, the baseline probe success rate is below 90%, or
the baseline error ratio is already above the limit. Without `--yes` you must
type `reslab-experiment` to confirm.

## Kill switch

```
python guardian.py --abort          # delete podchaos/networkchaos reslab-experiment
python guardian.py --abort --all    # delete every podchaos/networkchaos in the namespace
```

The guardian runs the same delete automatically on breach, on
`max_experiment_seconds`, on Ctrl+C, and when the experiment ends, then checks
the object is gone and measures recovery.

## Thresholds (`settings.json` -> `safety`)

| Key | Default | Meaning |
|---|---|---|
| `latency_multiplier` | 3 | Probe latency limit = max(baseline probe p95 x this, floor). |
| `latency_floor_ms` | 1500 | Minimum latency limit. The probe itself costs ~0.5s via kubectl. |
| `error_ratio_max` | 0.05 | Prometheus error ratio (checked every ~30s) that triggers a breach. |
| `consecutive_breaches` | 3 | Probe checks (2s apart) in a row that must be bad (failed or slow). |
| `max_experiment_seconds` | 90 | Hard time limit before the kill switch. |
| `recovery_timeout_seconds` | 180 | Give up measuring recovery after this long. |
| `recovery_healthy_probes` | 5 | Consecutive good probes (and deployment fully ready) that count as recovered. |

Notes: a `pod_kill` makes the storefront probe fail until the new pod is Ready,
so it may legitimately trip the breach check; the kill switch then deletes the
PodChaos object and recovery is measured. `recovery_seconds` counts from the
kill switch (or experiment end).
