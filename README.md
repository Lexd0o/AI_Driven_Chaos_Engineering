# ChaosPilot

**AI-Driven Chaos Engineering for Cloud-Native Resilience** - Bosch hackathon project (MIT Bengaluru, Oct 2026).

ChaosPilot studies a cloud-native app (the OpenTelemetry Demo on Kubernetes), finds likely weak points by **static analysis**, **proves or refutes** them with short, safety-capped **Chaos Mesh** experiments, **recommends fixes as code** (Kubernetes YAML patches), and **scores resilience**. The design rule is: **AI proposes, policy validates, executor acts; scores are computed by code, and the AI only explains.** The LLM never touches the cluster, never picks targets or parameters, and the whole pipeline still works with no LLM at all (deterministic fallback).

## Architecture

```mermaid
flowchart LR
    K8S[(k8s API)] --> D
    JG[(Jaeger traces)] --> D
    OV[graph_overrides.yaml] --> D
    D[discover<br/>graph.json] --> A[audit<br/>risks.json]
    A --> R[rank<br/>ranked_candidates.json]
    A --> F[fixes<br/>reports/fixes/*.yaml]
    R --> P[plan<br/>plan.json]
    LLM1{{LLM text only:<br/>hypothesis, reason,<br/>journeys}} -.-> P
    P --> POL[policy<br/>plan_validated.json]
    POL --> RUN[run<br/>one experiment, locked]
    RUN --> CM[Chaos Mesh via<br/>agents/chaos_agent_expanded]
    CM -.guardrail abort / watchdog.-> RUN
    RUN --> RR[(data/runs/*.json)]
    RR --> S[score<br/>scores.json]
    F --> S
    RR --> B[benchmark<br/>benchmark.json]
    S --> REP[report<br/>findings_report.md/.json]
    B --> REP
    LLM2{{LLM prose only (Groq),<br/>guards + template fallback}} -.-> REP
    S --> DASH[dashboard<br/>streamlit]
    RR --> DASH
    DASH -.STOP ALL.-> RUN
```

Principles: ranking and scoring are deterministic code; one experiment at a time (`data/run.lock`); every fault is time-capped and stoppable without the LLM.

## Modules

| File | What it does | Input -> output |
|---|---|---|
| `src/discover.py` | Builds the service dependency graph from the k8s API (workloads, replicas, probes, PDB/HPA) and Jaeger traces; applies manual corrections. Refuses to write a live graph with no trace edges unless `--allow-no-traces`. | k8s API, Jaeger, `config/graph_overrides.yaml`, `config/criticality.yaml` -> `data/graph.json`, `data/graph.mmd` |
| `src/audit.py` | Static checks (single replica/no PDB, no readiness/liveness probe, no limits, no HPA on shared dependency). Findings are *predictions*. | `graph.json` -> `data/risks.json` |
| `src/rank.py` | Deterministic risk score = criticality x centrality x (1 + risk flags); centrality = max(fan-in, gateway share) + checkout-path bonus (D-015); tie-breakers D-014. | `graph.json`, `risks.json`, `config/graph_overrides.yaml` (entry_points) -> `data/ranked_candidates.json` |
| `src/baseline.py` | Steady-state journey baseline (default 5 min, every 5 s; `--minutes`, `--interval`). GET probes plus the checkout probe (every 3rd tick, since it places a real fake order). Does not inject anything. | `config/executor.yaml` `journey_probes`, `config/planner.yaml` `baseline:` -> `data/baseline.json` |
| `src/plan.py` | Picks experiments from the ranked list + `config/fault_catalog.yaml`. Optional LLM (Groq `openai/gpt-oss-20b`, `config/planner.yaml`) writes only `hypothesis` and `reason`; journeys stay deterministic (D-012). Per-journey SLOs are derived from `data/baseline.json` (p95 x 2, floor 1000 ms; success min 0.95). `--deterministic` skips the LLM. | `ranked_candidates.json`, `risks.json`, `graph.json`, `baseline.json`, `config/planner.yaml` -> `data/plan.json` |
| `src/policy.py` | Validates every plan item against `config/policy.yaml`; rejected items are dropped (not clamped) and printed. | `plan.json` -> `data/plan_validated.json` (approved only) |
| `src/fixes.py` | Fix-as-code: Kubernetes patches per finding (probes, PDB, HPA); never applied automatically. | `risks.json`, `graph.json`, `config/fixes.yaml` -> `reports/fixes/*.yaml`, `reports/fixes/index.json` |
| `src/run.py` | Runs ONE approved experiment: re-checks policy, takes the lock, baseline check, injects via the vendored agent, guardrail, recovery watch, writes the run record. Also `--stop-all`. `--label <tag>` / `--note <text>` tag a run record (e.g. `--label fix-applied`) for before/after-fix comparison. Checkout is probed by a synthetic add-to-cart + place-order probe (`guardian.probe_checkout`, 6 s timeout, 5000 ms limit). | `plan_validated.json`, `config/executor.yaml` -> `data/runs/<exp>-r<n>.json` (+ agent report `data/runs/<run_id>.json`, `data/events.log`) |
| `src/score.py` | Deterministic scoring (D-008/D-017), findings ranking, coverage, before/after-fix `comparisons` for labelled runs. Works with zero runs. | `plan_validated.json`, `data/runs/*.json`, `graph.json`, `risks.json`, `ranked_candidates.json`, `reports/fixes/index.json`, `config/scoring.yaml` -> `data/scores.json` |
| `src/report.py` | Findings report; optional LLM explanations, validated (length, known `risk_id`, value-based number check, unknown-service and unmeasured-journey-failure rejection). `--no-llm` skips the LLM. | `scores.json`, `graph.json`, `ranked_candidates.json`, `benchmark.json` -> `reports/findings_report.md`, `reports/findings_report.json` |
| `src/benchmark.py` | Planner benchmark: position of the first confirmed weakness in our rank order vs 1000 seeded random orderings of the same run set. A hit = degraded/not_recovered outcome or aborted run (D-019). | `plan_validated.json`, `data/runs/*.json`, `config/scoring.yaml` -> `data/benchmark.json` |
| `src/llm.py` | Shared structured LLM call used by `plan.py` (Groq, per `config/planner.yaml` `provider`; Anthropic optional) and `report.py` (Groq, per `config/scoring.yaml` `llm.provider`); any failure returns `None` so callers keep deterministic text. Groq key from `GROQ_API_KEY` or repo-root `.env`. | prompt + pydantic schema -> parsed output or `None` |
| `dashboard/app.py`, `dashboard/dash_data.py` | Streamlit dashboard (read-only except STOP ALL); see Dashboard. | `data/*.json`, `data/runs/`, `reports/` |
| `agents/chaos_agent_expanded/` | Vendored third-party injector (PodChaos / NetworkChaos / StressChaos), guardian, detached watchdog. Driven only by `run.py`. See its README. | - |

## Safety layers

| Layer | Mechanism |
|---|---|
| Policy (`config/policy.yaml`) | Namespace allowlist (`otel-demo` only); `forbidden_targets` (kube-system, chaos-mesh, flagd, load-generator, otel-collector, jaeger, prometheus, grafana, opensearch, ...); `max_duration_s: 120`; allowed fault types only (pod_kill, network_delay <= 1000 ms, cpu_stress <= 4 workers); mechanism must match. Fails closed if `forbidden_targets` is missing. |
| `run.py` refusals | Re-runs the policy check on `--execute`; refuses services in `probe_path_services` (`frontend-proxy`, which carries the probe port-forward); refuses experiments with no GET probe for any expected journey (e.g. payment -> checkout only). |
| Executor pre-checks | Healthy baseline (no fault if any pre-sample fails), `can-i delete`, server-side dry run, one experiment at a time (`data/run.lock`). |
| Guardrail | Aborts when probes fail or are slower than 2000 ms for 10 s sustained (`config/executor.yaml`); sampled every 2 s. |
| Hard cap | Every timed Chaos Mesh spec carries `duration`; a detached watchdog deletes our CR at `duration + 15 s` even if the controller dies. |
| `--stop-all` | `python -m src.run --stop-all` deletes all chaos CRs in the namespace and creates `data/STOP` (blocks new runs; delete it manually to continue). No LLM involved. It does not reset feature flags (run.py injects none). |
| LLM limits | The planner LLM can only set three text fields; targets, fault types, params and ranking come from code. `report.py` drops any AI text containing a number that is not in its facts, naming an unknown service, or claiming failure on an unmeasured journey. |

## Prerequisites

- Docker Desktop with Kubernetes enabled (single node, context `docker-desktop`, as set in `config/executor.yaml`)
- OpenTelemetry Demo Helm chart installed in namespace `otel-demo`
- Chaos Mesh installed in namespace `chaos-mesh`. Use the Helm values in `config/cluster/` (Docker runtime, namespace filter) - see Run from scratch, Level B; `ChaosMonkeys/Requirementsandsetup.md` B6 has k3s flags that differ from Docker Desktop
- `kubectl` on PATH, Python 3.11+
- Python packages: `pip install -r requirements.txt` (kubernetes, requests, pyyaml, pydantic, anthropic, streamlit, pandas)
- Optional AI text: `GROQ_API_KEY` for plan hypotheses and report explanations (see Quickstart); `ANTHROPIC_API_KEY` only if you switch `config/planner.yaml` back to `provider: anthropic`. Without a key everything falls back to deterministic text.

## Quickstart

```bash
pip install -r requirements.txt
```

AI explanations (optional): create a file named `.env` in the repo root (it is gitignored) containing one line, `GROQ_API_KEY=<your key>`. Alternatively set the environment variable (PowerShell: `$env:GROQ_API_KEY = "..."`). Plan and report text use Groq `openai/gpt-oss-20b` (`config/planner.yaml` `provider: groq`, `config/scoring.yaml` `llm.provider: groq`). Never commit a key.

Open two terminals and keep both port-forwards running:

```bash
# 1. The shop + journey probes (used by run.py)
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080

# 2. Jaeger, queried directly by discover (via frontend-proxy it nearly OOMKilled the proxy)
kubectl -n otel-demo port-forward svc/jaeger 16686:16686
```

Web store: http://localhost:8080/ - Jaeger UI: http://localhost:16686/jaeger/ui/

Port 8080 is needed by `run.py` and the dashboard Live health tab; 16686 by `discover`.

## Run

Run from the repo root.

```bash
python -m src.discover        # writes data/graph.json + data/graph.mmd   (offline: --fixtures)
python -m src.audit           # writes data/risks.json
python -m src.rank            # writes data/ranked_candidates.json
python -m src.baseline        # 5 min steady state -> data/baseline.json (needs the 8080 port-forward)
python -m src.plan            # writes data/plan.json   (--deterministic = no LLM)
python -m src.policy          # writes data/plan_validated.json; prints REJECTED items
python -m src.fixes           # writes reports/fixes/*.yaml + index.json

python -m src.run --list                  # approved experiments, runnable or REFUSED
python -m src.run --exp exp-004           # mock: prints the Chaos Mesh manifest, no cluster
python -m src.run --exp exp-004 --preview # server-side dry run, nothing created
python -m src.run --exp exp-004 --execute # REAL injection -> data/runs/exp-004-r1.json
python -m src.run --exp exp-002 --execute --label fix-applied --note "probes applied"  # tag a post-fix replay
python -m src.run --stop-all              # emergency stop

python -m src.score           # writes data/scores.json
python -m src.benchmark       # writes data/benchmark.json (ranking vs random orderings)
python -m src.report          # writes reports/findings_report.md/.json  (--no-llm = no LLM)
```

Run `--execute` once per repeat (the repeat number is counted from existing `data/runs/<exp>-r*` files). `score` and `report` work with zero runs and then say "not scored". Example experiment ids come from your own `data/plan_validated.json`; use `--list`.

## Dashboard

```bash
streamlit run dashboard/app.py
```

Tabs: Overview, Dependency graph, Risk ranking, Experiments, Findings & fixes, Live health. The sidebar has a **STOP ALL CHAOS** button (tick the confirmation checkbox first) that runs `python -m src.run --stop-all`. The dashboard never starts experiments. It needs `streamlit` (in `requirements.txt`); the Live health tab needs the 8080 port-forward.

## Tests

```bash
python -m unittest discover -s tests -v
```

140 tests (verified passing 11 Oct): `test_run.py` (plan translation, refusals, run records, watchdog), `test_score.py`, `test_report.py`, `test_p3_fixes.py` (journey mapping, policy, rank, discover guard), `test_benchmark.py`, `test_dashboard.py`, `test_llm.py`, `test_baseline.py`, `test_checkout_probe.py`, `test_plan_llm.py`. They use no cluster or API key.

## Scoring

Per affected journey (Decisions.md D-008, details D-017, config in `config/scoring.yaml`):

```
S = 100 x (0.45*A + 0.20*L + 0.20*R + 0.15*C)
A = successful probes / probes during the fault window
L = share of probes within the latency SLO
R = 1 if survived; max(0, 1 - recovery_time / recovery_budget) if degraded and recovered; 0 if not recovered
C = share of NON-target journeys that stayed within SLO (containment)
```

- Journey weights: checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1. Overall score = sum(experiment weight x S) / sum(weight); an experiment's weight is the largest weight among its expected journeys.
- If no non-target journey was measured, C is undefined and the weights are renormalised over A/L/R.
- Severity of a tested finding = (100 - S) x service criticality: Critical >= 30, High >= 15, Medium >= 5, else Low.
- Categories: Redundancy (pod_kill), Dependency (network_delay), Capacity (cpu_stress).
- **No runs -> `resilience_score` is null and shown as "not scored"**, always with coverage ("0 of 8 planned experiments run"). Inconclusive runs are not scored.
- Finding status: `predicted` (audit only) -> `confirmed` / `refuted` / `inconclusive` after experiments. Confirmed findings rank first.

## Run from scratch

Commands are PowerShell (Windows, Docker Desktop Kubernetes). Two levels: **A** reuses the running cluster and re-runs the whole pipeline from a fresh clone; **B** also rebuilds the cluster.

### Level A - fresh clone, existing cluster

1. Clone into a new folder and create the environment:

```powershell
git clone -b p3-live-pipeline https://github.com/Lexd0o/AI_Driven_Chaos_Engineering
cd AI_Driven_Chaos_Engineering
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Create `.env` in the repo root (gitignored) with one line, `GROQ_API_KEY=<your key>`, or copy your existing `.env` over. Without a key the pipeline still runs with deterministic text.

3. Start both port-forwards, each in its own terminal, and leave them running (the loop restarts them if the pod behind them dies):

```powershell
while ($true) { kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080; Start-Sleep 3 }
while ($true) { kubectl -n otel-demo port-forward svc/jaeger 16686:16686; Start-Sleep 3 }
```

4. Check the cluster and the tests:

```powershell
kubectl -n otel-demo get pods          # all Running/Ready (flagd, product-catalog, checkout, jaeger ...)
kubectl -n chaos-mesh get pods
python -m unittest discover -s tests   # no cluster or key needed
```

5. Optional, to start with no previous results (a clone contains the committed `data/`): delete the files in `data\runs\` and `data\events.log` (keep the `data\runs` folder). `score` and `report` then say "not scored" until you run experiments.

6. Pipeline (each step writes the file named in the Run section):

```powershell
python -m src.discover
python -m src.audit
python -m src.rank
python -m src.baseline          # 5 min; needs the 8080 port-forward
python -m src.plan              # add --deterministic to skip the LLM
python -m src.policy
python -m src.fixes
```

7. Experiments, strictly one at a time. Wait until the app is healthy again (all pods Ready, shop loads) between runs:

```powershell
python -m src.run --list                      # runnable or REFUSED
python -m src.run --exp exp-002 --preview     # server-side dry run, nothing created
python -m src.run --exp exp-002 --execute     # real injection; run it again for repeat 2, 3, ...
python -m src.run --exp exp-002 --execute --label fix-applied --note "what changed"   # tag a post-fix replay
```

   If a pod stays stuck after a run (e.g. product-catalog at its memory limit), restart it: `kubectl -n otel-demo delete pod -l app.kubernetes.io/name=product-catalog`. Emergency stop: `python -m src.run --stop-all`, then `Remove-Item data\STOP` before the next run.

8. Score, benchmark, report, dashboard:

```powershell
python -m src.score
python -m src.benchmark
python -m src.report            # --no-llm for deterministic text only
streamlit run dashboard/app.py
```

To reproduce the "before" state after the product-catalog fix has been applied: `kubectl -n otel-demo rollout undo deploy/product-catalog`. To apply the generated fix again (one patch at a time; the node is nearly full): `kubectl -n otel-demo patch deployment product-catalog --patch-file reports/fixes/R-product-catalog-no_liveness_probe.yaml`, then the same with `R-product-catalog-no_readiness_probe.yaml`.

### Level B - rebuild the cluster

Values files exported from the live releases are in `config/cluster/`: `otel-demo-values.yaml` (chart `opentelemetry-demo` 0.42.3, app 3.1.0; optional AI components off, memory limits sized for flagd/load-generator/grafana) and `chaos-mesh-values.yaml` (chart 2.8.4, Docker runtime socket, namespace filter on, dashboard and DNS server off).

```powershell
# remove the old demo (Chaos Mesh can stay; then skip its install below)
helm uninstall otel-demo -n otel-demo
kubectl delete ns otel-demo

helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update

helm install chaos-mesh chaos-mesh/chaos-mesh --version 2.8.4 -n chaos-mesh --create-namespace -f config/cluster/chaos-mesh-values.yaml
helm install otel-demo open-telemetry/opentelemetry-demo --version 0.42.3 -n otel-demo --create-namespace -f config/cluster/otel-demo-values.yaml

# Chaos Mesh only acts on annotated namespaces; cap Jaeger's in-memory traces (it OOMs otherwise)
kubectl annotate ns otel-demo chaos-mesh.org/inject=enabled
kubectl -n otel-demo set env deploy/jaeger MEMORY_MAX_TRACES=10000
```

Then wait for all pods to be Ready (`kubectl -n otel-demo get pods -w`) and let the load generator run for 3-5 minutes so Jaeger has traces before `discover`. If a pod sits in `ImagePullBackOff` on a `ghcr.io` image, pull the image on the host (`docker pull <image>`), then delete the stuck pod so it restarts from the local image. The node is ~99% memory-requested, so do not add extra workloads. Then continue with Level A, step 3.

## Latest results (live campaign, 11 Oct 2026)

**Headline resilience score: 73.2 / 100**, 4 of 8 planned experiments scored (`data/scores.json`). Categories: **Redundancy 100**, **Dependency 86.1**, **Capacity 20.6**. Gates G1-G4 are met. With the product-catalog fix counted (see below) the same formula gives 70.8 overall (`after_fixes_score`).

**Baseline** (`src/baseline.py`, 5 min, 60 samples per journey, 20 for checkout): 100% success on all journeys. p95: browse 445 ms, cart 507, ads-recs 795, shipping-quote 436, checkout 1986. Per-journey SLOs derived from it (p95 x 2, floor 1000 ms; success min 0.95): browse 1000, cart 1014, ads-recs 1591, shipping-quote 1000, checkout 3972 ms. Checkout is now measured by a synthetic add-to-cart + place-order probe, so it is no longer an uncovered journey and payment's experiment (exp-008) is runnable (not yet run).

| Experiment | Fault | Target | Runs | Outcomes | Score S | Category |
|---|---|---|---|---|---|---|
| exp-004 | pod_kill | currency | 1 | survived | 100 | Redundancy |
| exp-006 | network_delay | shipping | 1 | survived | 100 | Dependency |
| exp-003 | network_delay | astronomy-db | 3 | degraded_recovered x3 | 72.3 | Dependency |
| exp-002 | cpu_stress | product-catalog | 7 (r1-r4 before the fix, r5-r7 after) | see below | 20.6 | Capacity |

**Repeats of exp-002 (cpu_stress on product-catalog):** r1 not_recovered; r2 degraded_recovered (~55 s); r3 inconclusive (pre-check refused: checkout slow, nothing injected); r4 not_recovered (the pod self-recovered after ~4 min, beyond the 120 s budget). exp-003 gave the same outcome all 3 times (degraded_recovered). exp-004 and exp-006 ran before the checkout probe existed, so their records still list checkout as uncovered.

**Confirmed finding:** under cpu_stress, product-catalog sits at its 32Mi memory limit and does not recover in time; the guardrail aborts the run. It had no liveness or readiness probe (confirms `R-product-catalog-no_liveness_probe`; the restart also exposed the missing readiness probe).

**Fix + replay (Tier 2, T2.1):** we applied the generated product-catalog liveness + readiness gRPC probe patches (one combined patch, same settings as `reports/fixes/R-product-catalog-no_*_probe.yaml`) and replayed exp-002 three times with `--label fix-applied`:

| Run | Outcome | What happened |
|---|---|---|
| r5 | not_recovered | Kubernetes restarted the pod automatically through the liveness probe (no manual restart), but not within the 120 s budget |
| r6 | inconclusive | pre-check unhealthy after the restart; nothing injected |
| r7 | inconclusive | probes breached ~3 s before Chaos Mesh confirmed the injection, so the executor records "no fault injected" (known measurement quirk, documented not fixed) |

Result: exp-002 S went **20.6 -> 10.9** (only one scored post-fix run) and the after-fixes overall score is 70.8. Honest reading: the fix works as designed (automatic recovery, no human needed) but is too slow for the 120 s budget with the generated probe timings (about 60 s to detect: 3 failures x 20 s period). The root cause is product-catalog's 32Mi memory limit. Recommendation: raise the memory limit and tighten the liveness probe (e.g. period 10 s, failureThreshold 2). The node has only ~39Mi of memory requests free, so memory has to be freed elsewhere first. One scored run is a small sample and not conclusive.

**Ranking benchmark** (`data/benchmark.json`): our order put the first confirmed weakness at position 1 of 4; the mean over 1000 seeded random orderings is 1.66, and 51% of random orderings do as well (2 hits among 4). At this sample size there is no evidence of an advantage over random order.

**Limits:** 4 of 8 experiments scored (exp-001, 005, 007, 008 not run; exp-001 is refused by design); most experiments have 1 repeat and repeats of the same experiment differ; containment (C) was not measured, so weights are renormalised over A/L/R; only 2 confirmed weaknesses; one scored post-fix run; numbers come from journey probes on a loaded single node, not Prometheus. AI explanation text varies run to run and only text passing every guard is kept.

## Current status (11 Oct 2026)

| Area | Status |
|---|---|
| Pipeline (discover to fixes, run.py + injector, score, report) | Built, verified live |
| Baseline (`src/baseline.py` -> `data/baseline.json`) and per-journey SLOs | Done |
| Checkout probe (`probe_checkout`) | Done; checkout is a measured journey |
| Live chaos campaign | 4 of 8 experiments scored; exp-002 and exp-003 repeated; resilience score 73.2 |
| Fix verification (`--label fix-applied`, `comparisons` in `scores.json`) | Done once (product-catalog probes); result in Latest results |
| Benchmark (`src/benchmark.py`) | Built; no advantage shown yet (small sample) |
| Dashboard (`dashboard/`) | Built |
| AI text | Groq `openai/gpt-oss-20b` for plan hypotheses/reasons and report explanations (journeys stay deterministic); guards and template fallback |
| Cluster reproducibility | `config/cluster/*.yaml` + Run from scratch |
| Gates | G1-G4 met (recorded in `ChaosMonkeys/progress.md`) |
| Remaining | Clean-run verification of Run from scratch, demo video, submission (team form, architecture page) |

## Known constraints

- The single node is ~99% memory-requested: adding workloads can evict app pods (flagd was evicted once; metrics-server was removed for this reason). Apply fixes one at a time.
- Jaeger is in-memory, capped at 10000 traces (about 30 min of history); a Jaeger restart wipes all traces.
- SLOs in `plan.json` come from `data/baseline.json` (p95 x 2, floor 1000 ms); re-run `python -m src.baseline` if the node load changes a lot.
- Checkout is probed with a synthetic POST journey that places a real (fake) order each poll; records made before the probe existed still list checkout under `uncovered_journeys`. Containment is not measured.
- Measurement quirk: if probes breach before Chaos Mesh confirms the injection, the run is recorded inconclusive ("no fault injected"), as in exp-002-r7.
- Numbers come from journey probes, not Prometheus.

## Repo layout

```
src/        discover audit rank baseline plan policy fixes run score benchmark report llm
dashboard/  app.py dash_data.py (Streamlit)
requirements.txt
config/     policy executor scoring planner fault_catalog fixes criticality graph_overrides (.yaml)
            cluster/ otel-demo-values.yaml chaos-mesh-values.yaml (Helm values for rebuilding the cluster)
agents/     chaos_agent_expanded/ (injector, vendored)   recovery_agent/ (third-party, not used by the pipeline)
tests/      test_run test_score test_report test_p3_fixes test_benchmark test_dashboard test_llm test_baseline test_checkout_probe test_plan_llm
fixtures/   graph.json (offline development)
data/       generated: graph, risks, ranked_candidates, baseline, plan, plan_validated, scores, benchmark, runs/
reports/    findings_report.md/.json, fixes/*.yaml
ChaosMonkeys/   project docs
opentelemetry-demo/   upstream demo source (target app)
```

## Documentation

All in [`ChaosMonkeys/`](ChaosMonkeys/): [CONTEXT.md](ChaosMonkeys/CONTEXT.md) (start here), [progress.md](ChaosMonkeys/progress.md) (status board), [Architecture.md](ChaosMonkeys/Architecture.md) (contracts, safety, scoring), [Decisions.md](ChaosMonkeys/Decisions.md), [Rules.md](ChaosMonkeys/Rules.md), [Requirementsandsetup.md](ChaosMonkeys/Requirementsandsetup.md), [techstack.md](ChaosMonkeys/techstack.md), [Experiments.md](ChaosMonkeys/Experiments.md), [Plan.md](ChaosMonkeys/Plan.md), [Demo.md](ChaosMonkeys/Demo.md).

Related work: ChaosEater (NTT), Krkn-AI (Red Hat), Gremlin Reliability Score, Steadybit Reliability Advice.
