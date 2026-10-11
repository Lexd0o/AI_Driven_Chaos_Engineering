# Architecture.md — ChaosPilot

Source for the 1-page architecture deliverable (components, how the AI decides, safety controls).

---

## 1. Core principle

> **The AI proposes → the policy decides → the executor acts.**

- The LLM only produces **JSON** (hypotheses, reasons, explanations). It never runs commands.
- Ranking and scoring are **deterministic code**, so results are repeatable and explainable.
- Every action is logged with an experiment ID.

---

## 2. Pipeline

```
DISCOVER → AUDIT → RANK → HYPOTHESISE → VALIDATE → INJECT → OBSERVE → SCORE → EXPLAIN → RECOMMEND → (REPLAY)
```

| Stage | Module | Input | Output |
|---|---|---|---|
| Discover | `src/discover.py` | k8s API, Jaeger traces, `config/graph_overrides.yaml` | `data/graph.json` |
| Audit | `src/audit.py` | `graph.json`, deployment specs | `data/risks.json` (predicted risks) |
| Rank | `src/rank.py` | `data/graph.json`, `data/risks.json`, `config/graph_overrides.yaml` (`entry_points`) | `data/ranked_candidates.json` |
| Hypothesise | `src/plan.py` | ranked candidates, `config/fault_catalog.yaml`, `config/planner.yaml` | `data/plan.json` (LLM text via `src/llm.py`; fallback = templates) |
| Validate | `src/policy.py` | `plan.json`, `config/policy.yaml` | `data/plan_validated.json` (approved items only; rejections + reasons are printed, not written) |
| Inject / Guard / Run | `src/run.py` + `agents/chaos_agent_expanded` (D-016) | one approved item, `config/executor.yaml` | run record `data/runs/<exp>-r<n>.json` (state machine, guardrail, watchdog). Planned `inject.py` / `guard.py` / `observe.py` were not built as separate modules; flag injection is not implemented |
| Score | `src/score.py` | `plan_validated.json`, `data/runs/*.json`, `graph.json`, `risks.json`, `ranked_candidates.json`, `reports/fixes/index.json`, `config/scoring.yaml` | `data/scores.json` (null score when no runs) |
| Explain | `src/report.py` | `data/scores.json`, `graph.json`, `ranked_candidates.json`, `data/benchmark.json` | `reports/findings_report.md` + `reports/findings_report.json` |
| Recommend | `src/fixes.py` | findings | YAML patches in `reports/fixes/` |
| Benchmark | `src/benchmark.py` (built) | `data/plan_validated.json`, `data/runs/*.json`, `config/scoring.yaml` | `data/benchmark.json` (first-hit position vs seeded random orderings; shown in the report) |
| UI | `dashboard/app.py` + `dashboard/dash_data.py` (built) | `data/*.json`, `data/runs/*.json`, `reports/` (JSON files, no SQLite) | Streamlit dashboard; the only action is STOP ALL (`python -m src.run --stop-all`) |

---

## 3. Component diagram

```
                 ┌──────────────────────────────────────────┐
                 │        Streamlit dashboard (+ STOP)      │
                 └──────────────▲───────────────────────────┘
                                │ reads
┌──────────┐ graph ┌────────┐ risks ┌──────┐ ranked ┌────────┐ plan(JSON) ┌────────┐
│ Discover ├──────►│ Audit  ├──────►│ Rank ├───────►│Planner ├───────────►│ Policy │
│k8s+Jaeger│       │(static)│       │(code)│        │ (LLM)  │            │validate│
└──────────┘       └────────┘       └──────┘        └────────┘            └───┬────┘
                                                                              │ approved
┌──────────┐ samples(2s) ┌──────────┐   ABORT   ┌───────────────────────────▼──────┐
│ Observe  ├────────────►│ Guardrail├──────────►│ Run state machine + Injector      │
│Prom+probe│             │ (code)   │           │ flags (flagd) | Chaos Mesh CRs    │
└────┬─────┘             └──────────┘           └───────────────────────────────────┘
     │
┌────▼─────┐    ┌───────────┐    ┌───────────────────────┐
│ SQLite   ├───►│ Scorer    ├───►│ Reporter (LLM explains│──► findings_report.md
│ runs.db  │    │ (code)    │    │ + cites evidence)     │──► fixes/*.yaml
└──────────┘    └───────────┘    └───────────────────────┘
```

Placement: the controller (our Python code), dashboard and SQLite run **outside** the target namespace. Only the app namespace is ever targeted.

---

## 4. Data contracts (FROZEN after S0 — change only via Decisions.md)

### 4.1 `data/graph.json`
```json
{
  "generated_at": "2026-10-10T12:00:00Z",
  "nodes": [
    {"name": "checkout", "kind": "service", "replicas": 1, "stateful": false,
     "has_readiness": true, "has_liveness": true, "has_limits": true,
     "has_pdb": false, "has_hpa": false, "criticality": 0.4}
  ],
  "edges": [
    {"from": "checkout", "to": "payment", "protocol": "grpc", "source": "jaeger|override", "calls": 54}
  ]
}
```
`calls` is optional (D-013): times the edge appeared in sampled Jaeger traces; absent on override edges, treat as 0.

### 4.2 `data/risks.json` (static audit = predictions)
```json
[
  {"risk_id": "R-cart-spof", "service": "cart", "check": "single_replica",
   "severity": "high", "evidence": "replicas=1, no PDB",
   "suggested_experiment": "pod_kill", "status": "predicted"}
]
```
`status` lifecycle: `predicted → confirmed | refuted | inconclusive`.

### 4.3 `data/plan.json` (one item per experiment)
```json
{
  "experiment_id": "exp-003",
  "rank": 1,
  "risk_id": "R-cart-spof",
  "fault_type": "pod_kill",
  "mechanism": "chaosmesh|flag",
  "target": {"namespace": "otel-demo", "service": "cart"},
  "params": {"duration_s": 60},
  "hypothesis": "Killing the only cart pod breaks add-to-cart until the pod is Ready again.",
  "reason": "cart: replicas=1, fan-in 2, on checkout path (criticality 0.4).",
  "expected_impact": {"journeys": ["cart", "checkout"]},
  "slo": {"success_rate_min": 0.95, "p95_ms_max": 1500},
  "recovery_budget_s": 120,
  "planner": "llm|deterministic"
}
```

### 4.4 Run record (SQLite table `runs`, also exported as JSON)
```json
{
  "experiment_id": "exp-003", "repeat": 1,
  "t_start": "...", "t_inject": "...", "t_breach": "...|null",
  "t_detect": "...|null", "t_clear": "...", "t_healthy": "...|null",
  "aborted": false, "abort_reason": null,
  "outcome": "survived|degraded_recovered|not_recovered|inconclusive",
  "peak_error_rate": 0.42, "peak_p95_ms": 2300,
  "journeys": {"browse": {"A": 0.99, "L": 0.97}, "cart": {"A": 0.61, "L": 0.55}},
  "evidence": {"trace_ids": ["..."], "prom_queries": ["..."]}
}
```

### 4.5 Metric sample (SQLite table `samples`)
```
ts | experiment_id | journey_or_service | success_rate | p50_ms | p95_ms | rps | pods_ready
```

---

## 5. Safety design (layers)

| Layer | Mechanism | Survives controller crash? | Tier |
|---|---|---|---|
| L0 Scope | Policy: namespace allowlist (`otel-demo` only), max 1 target, max intensity, max 120 s; Chaos Mesh restricted to enabled namespaces | n/a (preventive) | 0 |
| L1 Guardrail | `guard.py` every 2 s: abort on threshold breach (§6) | No | 0 |
| L2 Hard cap | `duration` set inside every Chaos Mesh spec → auto-expires | **Yes** | 0 |
| L3 Manual STOP | `python -m src.run --stop-all` (dashboard button planned): deletes all chaos CRs in the namespace and creates `data/STOP` (blocks new runs until removed). Does not reset feature flags - run.py injects none. No Makefile exists | n/a | 0 |
| L4 StatusCheck | Chaos Mesh Workflow `StatusCheck` with `abortWithStatusCheck: true` | **Yes** | 2 |

Notes:
- Feature-flag faults have **no built-in expiry** → the runner must always reset them in a `finally` block (not applicable yet: no flag faults are implemented; if added, `--stop-all` must reset them too). Measure flag-flip latency on Day 1.
- Chaos Mesh docs warn network faults can't be recovered if the controller can't reach the daemon — keep `chaos-mesh` namespace off-limits.
- Experiment lock: a row in SQLite (`lock` table) or a lock file; `run.py` refuses to start if held.

---

## 6. Guardrail thresholds (initial — tune after baseline)

| Signal | Abort when | Window |
|---|---|---|
| Checkout journey success rate | < 50% | 10 s sustained |
| Any journey p95 | > 3 × baseline p95 | 15 s sustained |
| Blast radius | a **non-target** journey drops below its SLO | 10 s |
| Statistical early warning | z-score > 4 vs baseline on error rate | 3 consecutive samples |
| Time | experiment exceeds `duration_s + 60 s` | — |

---

## 7. Measurement definitions

Timestamps per run:
- `t_inject` fault confirmed active · `t_breach` first SLO violation · `t_detect` guardrail/monitor noticed · `t_clear` fault removed · `t_healthy` start of ≥30 s continuous healthy window

Derived:
- Detection delay = `t_detect − t_breach`
- Impact recovery time = `t_healthy − t_breach`
- Post-fault recovery = `max(0, t_healthy − t_clear)`

Rules:
- No breach → **survived**, recovery time 0.
- Never healthy within window → **not recovered** (no fake number).
- Missing telemetry → **inconclusive**, no score.
- Only call it "mean recovery time" when averaging ≥2 runs.

---

## 8. Scoring (deterministic; weights declared before runs)

Per experiment (per affected journey, then weighted):

```
S = 100 × (0.45·A + 0.20·L + 0.20·R + 0.15·C)
A = correct successful requests / offered requests (during fault window)
L = fraction of requests within latency SLO
R = 1 if survived; max(0, 1 − recovery_time / recovery_budget) otherwise; 0 if not recovered
C = fraction of NON-target journeys that stayed within SLO (containment)
```

- Journey criticality weights (`config/criticality.yaml`): checkout 0.4, cart 0.3, browse 0.2, ads/recommendations 0.1.
- Overall score = Σ(weight × S) / Σ(weight), shown **with coverage** ("7 of 9 planned experiments run").
- Category breakdown: **Redundancy** (pod kills), **Dependency** (network/app failures), **Capacity** (stress/load).
- Severity of a finding = (100 − S) × criticality → Critical ≥ 30, High ≥ 15, Medium ≥ 5, else Low.

Why these weights: availability matters most to users; latency and recovery equally next; containment last. The criticality idea mirrors weighted SLO scoring used by Krkn's Resiliency Score. Weights are a team choice and are stated openly in the report.

---

## 9. Planner logic

1. Risk score per candidate = `criticality(service) × centrality(service) × (1 + Σ static_risk_flags)`.
   - centrality = `max(normalised fan-in, gateway share)` + "on checkout path" bonus, capped at 1 (D-015).
   - gateway share = fraction of the app users can no longer reach from `entry_points` (config/graph_overrides.yaml) if this service is down. Without it an entry point (frontend-proxy) has no in-cluster callers and scores 0.
   - Ties: blast radius → inbound `calls` → high-severity risks → name (D-014).
2. Map each top risk to an allowed fault from `fault_catalog.yaml`.
3. LLM receives: graph summary, risk list, baseline, catalog. Returns plan items (schema in §4.3). Validated with pydantic; invalid → dropped with reason.
4. Fallback: if the LLM fails, use template hypotheses and the same ranking.

Benchmark (Tier 1): after all experiments run once, compute the position of the first **confirmed critical** finding in our ranked order vs the mean over 1,000 random orderings. Report with a "small sample" caveat. This validates the **ranking**, not the LLM text.

---

## 10. Related work (for the architecture page)

- **ChaosEater** (NTT, ASE 2025): LLM agents automate the full chaos engineering cycle on Kubernetes, including generating manifests. *We differ: LLM picks from validated templates; never writes raw manifests.*
- **Krkn-AI** (Red Hat, CNCF Sandbox): genetic algorithm evolves experiments scored by SLO impact. *Future work for us.*
- **Gremlin Reliability Score / Krkn Resiliency Score:** 0–100 scores from test pass rates / weighted SLOs. *Our scoring follows the weighted-SLO idea.*
- **Steadybit Reliability Advice:** static Kubernetes best-practice checks that suggest experiments. *Our "predicted → confirmed" audit follows this pattern.*
