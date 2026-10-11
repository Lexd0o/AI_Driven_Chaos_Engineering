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
| `src/plan.py` | Picks experiments from the ranked list + `config/fault_catalog.yaml`. Optional LLM writes only `hypothesis`, `reason`, `expected_impact.journeys`. `--deterministic` skips the LLM. | `ranked_candidates.json`, `risks.json`, `graph.json`, `config/planner.yaml` -> `data/plan.json` |
| `src/policy.py` | Validates every plan item against `config/policy.yaml`; rejected items are dropped (not clamped) and printed. | `plan.json` -> `data/plan_validated.json` (approved only) |
| `src/fixes.py` | Fix-as-code: Kubernetes patches per finding (probes, PDB, HPA); never applied automatically. | `risks.json`, `graph.json`, `config/fixes.yaml` -> `reports/fixes/*.yaml`, `reports/fixes/index.json` |
| `src/run.py` | Runs ONE approved experiment: re-checks policy, takes the lock, baseline check, injects via the vendored agent, guardrail, recovery watch, writes the run record. Also `--stop-all`. | `plan_validated.json`, `config/executor.yaml` -> `data/runs/<exp>-r<n>.json` (+ agent report `data/runs/<run_id>.json`, `data/events.log`) |
| `src/score.py` | Deterministic scoring (D-008/D-017), findings ranking, coverage. Works with zero runs. | `plan_validated.json`, `data/runs/*.json`, `graph.json`, `risks.json`, `ranked_candidates.json`, `reports/fixes/index.json`, `config/scoring.yaml` -> `data/scores.json` |
| `src/report.py` | Findings report; optional LLM explanations, validated (length, known `risk_id`, value-based number check, unknown-service and unmeasured-journey-failure rejection). `--no-llm` skips the LLM. | `scores.json`, `graph.json`, `ranked_candidates.json`, `benchmark.json` -> `reports/findings_report.md`, `reports/findings_report.json` |
| `src/benchmark.py` | Planner benchmark: position of the first confirmed weakness in our rank order vs 1000 seeded random orderings of the same run set. A hit = degraded/not_recovered outcome or aborted run (D-019). | `plan_validated.json`, `data/runs/*.json`, `config/scoring.yaml` -> `data/benchmark.json` |
| `src/llm.py` | Shared structured LLM call used by `plan.py` (Anthropic) and `report.py` (Groq, per `config/scoring.yaml` `llm.provider`); any failure returns `None` so callers keep deterministic text. Groq key from `GROQ_API_KEY` or repo-root `.env`. | prompt + pydantic schema -> parsed output or `None` |
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
- Chaos Mesh installed in namespace `chaos-mesh` (see `ChaosMonkeys/Requirementsandsetup.md` B6; its k3s flags differ from Docker Desktop, so adjust the runtime settings)
- `kubectl` on PATH, Python 3.11+
- Python packages: `pip install -r requirements.txt` (kubernetes, requests, pyyaml, pydantic, anthropic, streamlit, pandas)
- Optional AI text: `GROQ_API_KEY` for report explanations (see Quickstart) and `ANTHROPIC_API_KEY` for plan text. Without them everything falls back to deterministic text.

## Quickstart

```bash
pip install -r requirements.txt
```

AI explanations (optional): create a file named `.env` in the repo root (it is gitignored) containing one line, `GROQ_API_KEY=<your key>`. Alternatively set the environment variable (PowerShell: `$env:GROQ_API_KEY = "..."`). Report text uses Groq `openai/gpt-oss-20b` (`config/scoring.yaml`, `llm.provider: groq`). Plan text still uses Anthropic: `ANTHROPIC_API_KEY`. Never commit a key.

Open two terminals and keep both port-forwards running:

```bash
# 1. The shop + journey probes (used by run.py)
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080

# 2. Jaeger, queried directly by discover (via frontend-proxy it nearly OOMKilled the proxy)
kubectl -n otel-demo port-forward svc/jaeger 16686:16686
```

Web store: http://localhost:8080/ - Jaeger UI: http://localhost:16686/jaeger/ui/

Port 8080 is needed by `run.py` and the dashboard Live health tab; 16686 by `discover`. Optional for plan text: `export ANTHROPIC_API_KEY=...` (PowerShell: `$env:ANTHROPIC_API_KEY = "..."`).

## Run

Run from the repo root.

```bash
python -m src.discover        # writes data/graph.json + data/graph.mmd   (offline: --fixtures)
python -m src.audit           # writes data/risks.json
python -m src.rank            # writes data/ranked_candidates.json
python -m src.plan            # writes data/plan.json   (--deterministic = no LLM)
python -m src.policy          # writes data/plan_validated.json; prints REJECTED items
python -m src.fixes           # writes reports/fixes/*.yaml + index.json

python -m src.run --list                  # approved experiments, runnable or REFUSED
python -m src.run --exp exp-004           # mock: prints the Chaos Mesh manifest, no cluster
python -m src.run --exp exp-004 --preview # server-side dry run, nothing created
python -m src.run --exp exp-004 --execute # REAL injection -> data/runs/exp-004-r1.json
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

89 tests (verified passing 11 Oct): `test_run.py` (plan translation, refusals, run records, watchdog), `test_score.py`, `test_report.py`, `test_p3_fixes.py` (journey mapping, policy, rank, discover guard), `test_benchmark.py`, `test_dashboard.py`, `test_llm.py`. They use no cluster or API key.

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

## Latest results (live campaign, 11 Oct 2026)

First live chaos campaign: **resilience score 73.0 / 100**, 3 of 8 planned experiments, 1 repeat each. Gate G4 (>= 3 fault types recorded) is met.

| Experiment | Fault | Target | Outcome | S | Category |
|---|---|---|---|---|---|
| exp-004 | pod_kill | currency | survived | 100 | Redundancy |
| exp-006 | network_delay | shipping | survived | 100 | Dependency |
| exp-002 | cpu_stress | product-catalog | not_recovered | 19.1 | Capacity |

**Confirmed finding:** under cpu_stress, product-catalog stayed stuck at its 32Mi memory limit; the guardrail aborted the run at 75% probe failures and the pod had to be restarted manually. It has no liveness probe (confirms `R-product-catalog-no_liveness_probe`); the restart also exposed that it has no readiness probe. exp-003 (network_delay on astronomy-db) was skipped in favour of exp-006 to avoid re-breaking product-catalog.

**Ranking benchmark:** our order put the first confirmed weakness at position 1 of 3; the mean over 1000 seeded random orderings is 1.96, and 36% of random orderings do as well. Small sample, not statistically significant.

**Limits:** 1 repeat per experiment (no repeat statistics); the checkout journey is unmeasured (no GET probe, listed as uncovered); containment (C) was not measured, so weights are renormalised over A/L/R; only 3 experiments and 1 confirmed weakness. AI explanation text varies run to run and only text passing every guard is kept.

## Current status (11 Oct 2026)

| Area | Status |
|---|---|
| Pipeline (discover to fixes, run.py + injector, score, report) | Built, verified live |
| Live chaos campaign | 3 of 8 experiments run; resilience score 73.0; G4 met |
| Benchmark (`src/benchmark.py`) | Built |
| Dashboard (`dashboard/`) | Built |
| AI report text | Groq `openai/gpt-oss-20b` with guards and template fallback; planner on Anthropic (model `claude-opus-5-5` provisional, O-2) |
| Remaining | `baseline.json` (P2), repeats x3, Tier-2 fix + replay, checkout probe, video/submission |

The Chaos Mesh smoke gates G1-G3 are not yet recorded as passed in `ChaosMonkeys/progress.md`.

## Known constraints

- The single node is ~99% memory-requested: adding workloads can evict app pods (flagd was evicted once; metrics-server was removed for this reason). Apply fixes one at a time.
- Jaeger is in-memory, capped at 10000 traces (about 30 min of history); a Jaeger restart wipes all traces.
- Checkout median latency is about 21 s on the overloaded node, so SLOs (`slo` in `plan.json`) need a real baseline before the numbers mean much.
- `checkout` has no GET probe: experiments are probed on their other journeys and list checkout under `uncovered_journeys`. Containment is not measured either.
- Numbers come from journey GET probes, not Prometheus.

## Repo layout

```
src/        discover audit rank plan policy fixes run score benchmark report llm
dashboard/  app.py dash_data.py (Streamlit)
requirements.txt
config/     policy executor scoring planner fault_catalog fixes criticality graph_overrides (.yaml)
agents/     chaos_agent_expanded/ (injector, vendored)   recovery_agent/ (third-party, not used by the pipeline)
tests/      test_run test_score test_report test_p3_fixes test_benchmark test_dashboard test_llm
fixtures/   graph.json (offline development)
data/       generated: graph, risks, ranked_candidates, plan, plan_validated, scores, benchmark, runs/
reports/    findings_report.md/.json, fixes/*.yaml
ChaosMonkeys/   project docs
opentelemetry-demo/   upstream demo source (target app)
```

## Documentation

All in [`ChaosMonkeys/`](ChaosMonkeys/): [CONTEXT.md](ChaosMonkeys/CONTEXT.md) (start here), [progress.md](ChaosMonkeys/progress.md) (status board), [Architecture.md](ChaosMonkeys/Architecture.md) (contracts, safety, scoring), [Decisions.md](ChaosMonkeys/Decisions.md), [Rules.md](ChaosMonkeys/Rules.md), [Requirementsandsetup.md](ChaosMonkeys/Requirementsandsetup.md), [techstack.md](ChaosMonkeys/techstack.md), [Experiments.md](ChaosMonkeys/Experiments.md), [Plan.md](ChaosMonkeys/Plan.md), [Demo.md](ChaosMonkeys/Demo.md).

Related work: ChaosEater (NTT), Krkn-AI (Red Hat), Gremlin Reliability Score, Steadybit Reliability Advice.
