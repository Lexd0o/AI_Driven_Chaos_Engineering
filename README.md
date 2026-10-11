# ChaosPilot: AI-driven chaos engineering for cloud-native resilience

ChaosPilot decides what to break in a Kubernetes app, breaks it safely, measures what users would feel,
and reports how resilient the app is, with a ready-to-apply fix for each weakness.
Built for the Bosch hackathon (MIT Bengaluru, 10-11 Oct 2026) on the [OpenTelemetry Demo](https://opentelemetry.io/docs/demo/).

**Safe by design: AI proposes, policy decides, code acts.**
The LLM (optional) only writes hypothesis text. It never touches the cluster and never computes a score.
Targets, fault types, parameters and every number in the report come from deterministic code.

## How it works

```
discover -> audit -> rank -> plan -> policy     (static: what is risky, what to test first)
                                  |
                                  v
        src/run.py  ->  src/chaos/controller.py  ->  Chaos Mesh fault (PodChaos / NetworkChaos / StressChaos)
        policy re-check   inject, confirm, guardian      |
        refusals, STOP    + kill switch, cleanup,        v
                          watchdog                  P2 sampler: browse / cart / checkout probes every 2 s
                                  |
                                  v
        src/runmetrics.py -> src/score.py -> src/report.py (+ src/benchmark.py) -> reports/findings_report.md
```

| Step | Command | Output |
|---|---|---|
| Map services and calls from traces | `python -m src.discover` (offline: `--fixtures`) | `data/graph.json` |
| Static audit of predicted risks | `python -m src.audit` | `data/risks.json` |
| Rank what to test first | `python -m src.rank` | `data/ranked_candidates.json` |
| Plan experiments | `python -m src.plan` (`--deterministic` = no LLM) | `data/plan.json` |
| Policy check (drop anything out of scope) | `python -m src.policy` | `data/plan_validated.json` |
| Fix-as-code suggestions | `python -m src.fixes` | `reports/fixes/` |
| Run one approved experiment | `python -m src.run --exp exp-004 --execute` | `data/runs/<exp>-r<n>.json`, `data/runs.db` |
| Score the runs | `python -m src.score` | `data/scores.json` |
| Findings report with benchmark | `python -m src.report` | `reports/findings_report.md` |
| Dashboard | `streamlit run dashboard/app.py` | live view, map, runs, findings |

## Safety layers

| Layer | What | Needs the LLM |
|---|---|---|
| L0 policy | Only the `otel-demo` namespace; forbidden targets; caps on duration, latency and loss (`config/policy.yaml`); re-checked in `src/run.py` | No |
| Refusals | Never faults the service that carries the measurement path (`frontend-proxy`); refuses when nothing can be measured; refuses an unhealthy shop (checked on 20 s of probes, re-checked up to 2 times) | No |
| L1 guardian | Aborts on 3 failing probes, error ratio or time limit, then verifies the fault object is gone | No |
| L2 duration + watchdog | Every timed fault has a `duration`; a detached watchdog deletes our fault object at the deadline even if the controller dies | No |
| L3 STOP | `python -m src.run --stop-all` (or the dashboard button) blocks new runs and deletes every chaos object in the namespace | No |

One experiment at a time. Nothing is injected until you type the confirmation word.

## Setup (Windows, Docker Desktop Kubernetes; needs about 8 GB for the cluster)

Run from the repo root in PowerShell. Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

1. Turn on Kubernetes in Docker Desktop, then `kubectl config use-context docker-desktop` and install Helm (`winget install Helm.Helm`).
2. Install the OpenTelemetry Demo from the pinned values file. Do not use `--reuse-values` or `--set` afterwards (D-034):
   ```powershell
   helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
   helm repo update
   helm install otel-demo open-telemetry/opentelemetry-demo -n otel-demo --create-namespace --version 0.42.3 -f config/otel-demo-values.yaml
   ```
   The values file turns off OpenSearch, host metrics and the optional services, and sends collector logs to `debug` only.
   With the chart default, failing log exports blocked the quote service and broke checkout.
3. Install Chaos Mesh (Docker runtime). Chart version: see `ChaosMonkeys/techstack.md`.
   ```powershell
   helm repo add chaos-mesh https://charts.chaos-mesh.org
   helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace --set chaosDaemon.runtime=docker --set chaosDaemon.socketPath=/var/run/docker.sock --set controllerManager.replicaCount=1 --set dashboard.create=false
   ```
4. Wait until `kubectl -n otel-demo get pods` shows everything `Running`.

Full setup notes, fixes for Docker Desktop and the reasons behind them: `ChaosMonkeys/Requirementsandsetup.md`.

## Running an experiment

Keep three PowerShell windows open for the port-forwards (8081 because 8080 is taken on some laptops):

```powershell
kubectl -n otel-demo port-forward svc/frontend-proxy 8081:8080
kubectl -n otel-demo port-forward svc/prometheus 9090:9090
kubectl -n otel-demo port-forward svc/jaeger 16686:16686
```

Jaeger is read directly on 16686, not through frontend-proxy: heavy trace queries through the proxy made the
in-memory Jaeger run out of memory and restart, which wipes every stored trace.

In the repo root, with the venv active (set the same variables in the dashboard's window):

```powershell
$env:PROBE_BASE_URL="http://localhost:8081"
$env:PROMETHEUS_URL="http://127.0.0.1:9090"
$env:JAEGER_URL="http://localhost:16686/jaeger/ui/api/v3"

python -m src.run --list                              # which approved experiments are runnable, and why not
python -m src.chaos.k8s_monitor --baseline 60         # guard baseline (must be under 30 min old)
python -m src.run --exp exp-004 --preview             # server-side dry run, nothing created
python -m src.run --exp exp-004 --execute             # real fault; asks for the confirmation word
python -m src.score
python -m src.report
```

Do not run a second sampler (`python -m src.observe`) during a run: the controller starts its own and refuses if it sees another.

## Results

See `reports/findings_report.md`, generated by `python -m src.report` from the run files and stored samples.
Scoring (D-008): `S = 100 x (0.45 A + 0.20 L + 0.20 R + 0.15 C)`, always shown with coverage.
Experiments with too little data are reported as inconclusive and get no score.

## Tests

```powershell
python -m unittest discover tests
```

Offline: no cluster or network needed. Test data is synthetic and never reported as a result.

## Repo map

| Path | What |
|---|---|
| `src/` | pipeline (discover, audit, rank, plan, policy, fixes), run path (`run.py`, `chaos/`), measurement (`observe.py`, `runmetrics.py`), scoring (`score.py`, `report.py`, `benchmark.py`) |
| `config/` | policy, criticality, journeys and SLOs, chaos settings, executor, cluster Helm values |
| `agents/chaos_agent_expanded/` | P4's watchdog (used); recovery agent kept for reference only |
| `dashboard/` | Streamlit dashboard |
| `reports/fixes/` | suggested Kubernetes patches (never applied automatically) |
| `ChaosMonkeys/` | Architecture, Decisions, Rules, setup notes, demo script, progress log |
| `data/` | local runs, samples and generated files (git-ignored: never committed) |

## Known limits

- Results come from one single-node Docker Desktop cluster and a small number of runs; repeats are shown where they exist.
- The shop shows a low background error rate (a lone fast 503 every few minutes) even with no fault; the source is not found yet. The scorer does not count a blip that appears after a fault ended.
- A pod kill is instant, so its fault window holds few samples; the guardian can also stop a run early, which leaves it unscored.
- `frontend-proxy` cannot be tested on this setup because it carries the measurement port-forward.
- The planner benchmark compares the ranked order with random orders on the experiments scored so far (small sample).

Rules and decisions: `ChaosMonkeys/Rules.md`, `ChaosMonkeys/Decisions.md`.
