# P2_Observability.md — Role brief for P2 (Joy)

> Paste with `CONTEXT.md` + `progress.md` + `Decisions.md` when switching models for P2 work.

## 1. Mission

**Turn the app's signals into trustworthy numbers.** Every other role depends on your data:
- P3 needs your **trace edges** to build the dependency map.
- P4's **guardrail** decides to abort based on your live metrics.
- P5's **scores and report** are computed from your samples and evidence.
- The **dashboard** (yours) is what judges see in the video.

If your numbers are wrong, everything downstream is wrong. Accuracy over features.

## 2. Responsibilities

| # | Responsibility | Output | Consumers | Status |
|---|---|---|---|---|
| R1 | Telemetry access: Prometheus + Jaeger APIs | `src/telemetry.py` | everyone | ✅ (Jaeger v3) |
| R2 | Find & document real metric names and Jaeger endpoints | `Requirementsandsetup.md` B5.1 | P4, P5 | ✅ written into B5.1 (11 Oct) |
| R3 | **Journey probe**: browse / cart / checkout every 2 s | `src/probe.py` | P4, P5 | ✅ |
| R4 | **Sampler loop** → SQLite `samples` | `src/observe.py` | P4, P5, dashboard | ✅ |
| R5 | **Baseline**: steady-load capture | `data/baseline.json` | P3, P4, P5 | ✅ code · ❌ no valid baseline (needs team machine) · ⏳ `services` section empty |
| R6 | **Trace-based edges** | `edges_from_traces()` | P3 | ✅ |
| R7 | **Guardrail inputs**: `latest_sli()`, `zscore()` | `src/observe.py` | P4 | ✅ (tested) |
| R8 | **Recovery detection**: `find_breach()`, `find_healthy()` | `src/observe.py` | P4, P5 | ✅ (tested) |
| R9 | **Evidence collector** | `src/evidence.py` | P5 | ✅ |
| R10 | **Data quality** flag per run | `quality` in run metrics | P5 | ✅ `src/runmetrics.py` (8 tests) |
| R11 | **Streamlit dashboard** incl. STOP | `dashboard/app.py` | judges, video | ✅ v0 · ⏳ polish, real stop command |
| R12 | Fixtures for offline dev | `fixtures/` | P3, P5 | ⏳ (needs team-machine data; fake-shop data only if labelled synthetic) |

Not yours: injecting faults (P4), deciding thresholds' values (P4 with your data), scoring formula (P5), installing the cluster (P1).

## 3. Interfaces you provide

```python
# src/telemetry.py
load_config() -> dict                          # config/journeys.yaml + env overrides PROBE_BASE_URL, PROMETHEUS_URL, JAEGER_URL
prom_query(promql: str, at: float | None = None) -> list[dict]
prom_range(promql: str, start: float, end: float, step_s: int = 2) -> list[dict]
prom_metric_names(contains: str = "") -> list[str]
jaeger_services() -> list[str]
jaeger_traces(service: str, start: float, end: float, limit: int = 50, error_only: bool = False) -> list[dict]
#   -> [{"trace_id", "duration_ms", "error",
#        "spans": [{"span_id","parent_id","service","name","kind","url","error","start_ns","end_ns"}]}]
edges_from_traces(start: float, end: float, limit_per_service: int = 50) -> list[dict]
#   -> [{"source", "target", "count"}]   (caller -> callee; P3 maps to from/to, D-017)

# src/probe.py
run_journey(name: str, cfg: dict | None = None) -> dict   # {"ok": bool, "latency_ms": float, "status": int}; status 0 = timeout
journey_user(name: str, cfg: dict) -> str                 # "<probe_user>-<journey>" (D-018)
probe_browse() / probe_cart() / probe_checkout() -> dict

# src/observe.py
connect(db_path=None) -> sqlite3.Connection               # WAL mode
run_sampler(experiment_id: str | None, stop_after_s: int | None, db_path=None) -> None
sample_services(cfg, ts, experiment_id) -> list[tuple]    # per-service Prometheus rows; [] if unreachable
read_samples(experiment_id: str, db_path=None) -> list[dict]
latest_sli(journey: str, window_s: int = 10, db_path=None) -> dict   # success_rate, p50_ms, p95_ms, n (n==0 = NO DATA)
zscore(value, mean, std, min_std) -> float
find_breach(samples, slo) -> float | None
find_healthy(samples, slo, after_ts, sustain_s=30) -> float | None
# slo = {"journey": "checkout", "min_success": 0.9, "max_p95_ms": 1500, "window_s": 10, "min_n": 3}

# src/baseline.py
build_baseline(rows, start, duration_s, window_s=10, min_success=0.99, min_n=3) -> dict   # pure, tested
capture_baseline(duration_s=600, window_s=10, out_path=None, db_path=None) -> dict      # writes data/baseline.json

# src/runmetrics.py   (Architecture.md §7; recovery definition = D-024)
make_slos(journeys=None, min_success=0.95, max_p95_ms=1500, window_s=10, min_n=3) -> dict   # {journey: slo dict}
compute_run_metrics(rows, targets, slos, t_inject, t_clear, recovery_budget_s=120, observed_until=None,
                    sample_interval_s=2, min_coverage=0.8, max_gap_s=10, sustain_s=30) -> dict      # pure, tested
#   keys: targets, t_inject, t_clear, t_breach, t_healthy,
#         outcome ("survived"|"degraded_recovered"|"not_recovered"|"inconclusive"),
#         impact_recovery_s, post_fault_recovery_s, peak_error_rate, peak_p95_ms,
#         journeys {name: {"A", "L", "n"}}, containment_C, quality, issues
collect_run_metrics(experiment_id, targets, t_inject, t_clear, slos=None, recovery_budget_s=120, db_path=None, **kw) -> dict
#   CLI: python -m src.runmetrics --experiment ID --targets checkout --inject <epoch> --clear <epoch>

# src/evidence.py
collect_evidence(experiment_id, start, end, services=None, slow_ms=1500, max_ids=10, limit=1000) -> dict
#   keys: experiment_id, window, trace_ids (failed first, then slow), prom_queries, failed_traces, slow_traces,
#         counts{traces_seen, failed, slow, ignored_long_lived, ignored_own_queries, slow_ms},
#         error_services, top_error_services, quality ("ok"|"partial"), notes (lower quality), info (harmless)
#   default search service = frontend-proxy (all user requests). Never raises.
```

### `data/baseline.json` (produced by `build_baseline`)
```json
{
  "captured_at": "2026-10-10T14:00:00Z", "duration_s": 600, "window_s": 10,
  "load": {"vus": "<LOAD_VUS env>"},
  "journeys": {
    "checkout": {"success_mean": 0.99, "success_std": 0.01, "p50_ms": 180, "p95_ms": 420, "p95_std": 35, "n": 300, "success_overall": 0.99}
  },
  "services": {},
  "valid": true,
  "issues": []
}
```
(values above are illustrative.) `services` is still empty — planned: `{"payment": {"error_rate_mean", "error_rate_std", "p95_ms"}}`. Never use a baseline with `"valid": false`. Use `min_std` (e.g. 1% success, 10 ms latency) in z-scores.

## 4. Key technical notes

- **Probe targets** go through `frontend-proxy` (port 8080 on the team setup; 8081 on Joy's laptop via env var, both for Compose and for the local Kubernetes port-forward).
- **Two probes, two jobs** (D-023): P2's HTTP journey probes feed scores; P1's kubectl probe (`src/chaos/k8s_monitor.py`) only feeds the guardian.
- **Recovery for scores** (D-024): ≥30 s continuous healthy window in P2 samples; the guardian's 5-probe check never enters a score.
- **Probe vs load generator:** keep the load generator on for realistic traffic; the probe is our ground truth for user journeys. 1 request per journey per 2 s.
- **No probe retries** (D-018): rare one-off failures (Envoy `UC` 503s) are real background noise; the baseline's ≥99% bar absorbs them; the guardrail must use sustained windows.
- **Clock:** UTC epoch seconds from the machine running the controller.
- **Jaeger v3** (D-017): services at `/services`; traces at `/traces` with `query.service_name`, `query.start_time_min/max` (RFC3339), `query.num_traces`. 404 "No traces found" = empty. Our own Jaeger calls appear as frontend-proxy traces → excluded in evidence (D-019).
- **Checkout memory** (D-020): default 20 MiB + `GOMEMLIMIT=16MiB` caused GC thrashing locally. Check on the team machine.

## 5. Sprints

Times are relative: D1 H0 = Day-1 start; T = submission deadline. **Day 1 is one 4–5 h hard time-box (Sprint 1).** Day-2 sprints are ~4 h. Much of the code below is already done (see §2 / §6); the remaining Day-1 work is mostly on the team machine.

### Sprint 1 · D1 H0–H4:30 (hard limit H5:00) · "See it, baseline it, break it once"

| Step | Time | Task | Done when |
|---|---|---|---|
| 1.1 | 0:00–0:30 | Kick-off (CP0); confirm `baseline.json` + `samples` schemas with P3/P4/P5 | Schemas agreed |
| 1.2 | 0:30–2:00 | (Code already done.) Help P1; set env URLs for the team machine | URLs known |
| 1.3 | 2:00–2:30 | Shop loads (CP1): `python -m src.probe` on the team machine | Probes ok |
| 1.4 | 2:30–3:00 | Sampler live; `python -m src.telemetry` shows metrics + Jaeger services; write names/paths in `Requirementsandsetup.md` B5 | Rows every 2 s |
| 1.5 | 3:00–3:15 | **5-min baseline** (lock held, load on, no faults, success ≥99%) | `baseline.json` valid |
| 1.6 | 3:15–3:45 | Hand helpers + SLO dict to P4; watch first flag injection, then pod kill | Clear dip in the right journey |
| 1.7 | 3:45–4:15 | **Deliberate breach test** with P4 (G3) | Logged auto-abort + measured recovery |
| 1.8 | 4:15–4:30 | Save 5 min of real samples to `fixtures/`; demo baseline + dip (CP2) | Fixtures pushed; demo done |
| — | 4:30–5:00 | Buffer; cut order: baseline 5→3 min, drop pod kill, one rehearsal | — |

**Never cut:** shop running · a baseline · one failure observed · breach test.

### Sprint 2 · Day-1 bonus window
Edges, per-service metrics and evidence are already done. Use the time for `collect_run_metrics()` (if not done) and the `services` baseline section.

### Sprint 3 · D2 H0–H4 · "Evidence for every run"

| Step | Task | Done when |
|---|---|---|
| 3.1 | **10-min fresh baseline** (< 30 min old before runs) | Fresh valid baseline |
| 3.2 | `collect_evidence()` called for every run (wire with P4) | Each run record has evidence |
| 3.3 | `collect_run_metrics()`: A, L, containment C of non-target journeys, `quality` flag | P5's scorer runs on real data |
| 3.4 | Watch data quality during P4's 6–8 experiments; mark gaps inconclusive | No silent gaps |
| 3.5 | Dashboard reads real runs | Runs tab matches DB |

### Sprint 4 · D2 H4 → T−3h · "Make it visible"

| Step | Task | Done when |
|---|---|---|
| 4.1 | **Live**: fault-active shading, threshold lines, guardrail events | Watching a run is self-explanatory |
| 4.2 | **Map**: layered layout; colour by predicted / confirmed / refuted | Matches `risks.json` |
| 4.3 | **Runs**: experiments, outcome, recovery time, score | Matches DB |
| 4.4 | **Findings**: report, overall score + coverage, benchmark | Matches `reports/findings_report.md` |
| 4.5 | **STOP** button with the real stop command, tested once | Tested |
| 4.6 | Support backup video | Backup video exists |

### Sprint 5 · T−3h → T · "Release" (no new features)
Bug fixes only · README sections (observability setup, dashboard launch, metric names, env vars) · clean run from scratch with P1 · video-ready screens at 1080p · libraries in `techstack.md`.

## 6. Status as of 11 Oct, 01:45 IST (verified on Joy's laptop)

### Where things are
- **Team repo:** `https://github.com/Lexd0o/AI_Driven_Chaos_Engineering` (owner `Lexd0o`; older docs had `Lexd0`).
  - Branch `p2-observability`: all P2 code incl. `src/runmetrics.py`, `tests/test_runmetrics.py`, `tools/dry_run.py` (commit d8063e4).
  - Branch `integrate-chaos` (from `p2-observability`): Stage 1 of D-025 — P1's agent as `src/chaos/`, `requirements.txt`, `.gitignore` additions. Check `git status` / `git log --oneline -5` that it is pushed.
  - Branch `chaos-agent` (P1, c07af61): source of the port. Read-only worktree at `..\p1_chaos_agent` (remove with `git worktree remove "..\p1_chaos_agent"`).
- **Joy's local copy:** `C:\Users\Joy Mandal\Desktop\ChaosMonkeys Observability\AI_Driven_Chaos_Engineering` (Windows, VS Code, PowerShell, venv `.venv`, Python 3.12).
- **Local Kubernetes (current, D-021):** Docker Desktop Kubernetes, kubeadm, context `docker-desktop`, namespace `otel-demo`, Chaos Mesh in `chaos-mesh`. Install commands and fixes: `Requirementsandsetup.md` B11.
  - Port-forwards (each in its own terminal, keep open; restart after closing terminals):
    ```powershell
    kubectl -n otel-demo port-forward svc/frontend-proxy 8081:8080
    kubectl -n otel-demo port-forward svc/prometheus 9090:9090
    ```
    The Prometheus one must print both `127.0.0.1:9090` and `[::1]:9090`; if only one appears, something else holds the port (D-026).
  - Shop `http://localhost:8081`, Jaeger UI `/jaeger/ui`, flag UI `/feature`, Prometheus `http://127.0.0.1:9090`.
  - P1's code (`src/chaos/`) needs no port-forward (kubectl API proxy).
- **Local Docker Compose demo (stopped):** `C:\Users\Joy Mandal\Desktop\opentelemetry-demo`. Never run it together with the cluster (same ports). If needed again: stop the cluster port-forwards, then
  `docker compose -f compose.yaml -f compose.observability.yaml -f compose.joy-local.yaml up --force-recreate --remove-orphans --detach`
  (`compose.joy-local.yaml`, local only, never commit: checkout memory 80M, `GOMEMLIMIT=60MiB`, D-020). Stop it with the same `-f` files (`docker compose -f compose.yaml -f compose.observability.yaml -f compose.joy-local.yaml down`); a plain `docker compose down` left the observability containers (prometheus, jaeger, grafana, opensearch, opamp-server) running and holding ports.
- **Env vars for the repo terminal** (last only for that terminal; same values for cluster and Compose):
  ```powershell
  $env:PROBE_BASE_URL="http://localhost:8081"
  $env:PROMETHEUS_URL="http://127.0.0.1:9090"
  $env:JAEGER_URL="http://localhost:8081/jaeger/ui/api/v3"
  ```
  [verify] the Jaeger v3 API path through the cluster's frontend-proxy (the UI loads; `python -m src.telemetry` not yet run on the cluster).

### P2 files (in the repo)
| File | What it does | Status |
|---|---|---|
| `.gitignore` | `data/`, `*.db`, `*.db-wal`, `*.db-shm`, `__pycache__/`, `*.pyc`, `.venv/`, `.venv*/`, `venv/`, `.env`, `.env.*` | ✅ |
| `config/journeys.yaml` | URLs, timeouts, journeys, per-service PromQL (`[2m]`), `probe_user` prefix, stop command; `jaeger_url: http://localhost:8080/jaeger/ui/api/v3` | ✅ (`stop_command` still `make stop-all`, needs P1) |
| `src/telemetry.py` | Prometheus + Jaeger v3; `jaeger_traces` normalised shape incl. span `url`; `edges_from_traces` | ✅ |
| `src/probe.py` | `run_journey`, `journey_user`, `probe_*`; one user per journey; default timeout 5 s | ✅ |
| `src/observe.py` | `samples` table (WAL); sampler; SLI + breach/recovery helpers | ✅ |
| `src/baseline.py` | `build_baseline` (pure) + `capture_baseline`; `valid` + `issues` | ✅ code · `services` empty · no valid baseline |
| `src/evidence.py` | `collect_evidence()`; ignores >60 s traces and own Jaeger calls; notes vs info | ✅ |
| `tests/test_p2.py` | 16 tests: zscore, find_breach, find_healthy, build_baseline (synthetic data). `python -m tests.test_p2` | ✅ 16/16 |
| `src/runmetrics.py` | Per-run A / L / containment C / outcome / quality from `samples` (D-024) | ✅ |
| `tests/test_runmetrics.py` | 8 tests. `python -m tests.test_runmetrics` | ✅ 8/8 |
| `tools/dry_run.py` | Fake shop + sampler + 100 % checkout failure for `--fault-s` → run metrics + evidence → `data/dry_run_report.json`. SYNTHETIC, never reported | ✅ |
| `src/chaos/` (branch `integrate-chaos`) | P1's agent: `k8s_monitor`, `executor`, `guardian`, `controller`, `planner`, `app_mapper` (D-025) | ✅ Stage 1 · ⏳ Stage 2 |
| `tests/test_guardian.py`, `tests/test_chaos_port.py` | P1's 12 guardian tests + 7 port checks. `python -m unittest tests.test_guardian -v`, `python -m tests.test_chaos_port` | ✅ |
| `requirements.txt` | numpy, requests, pyyaml, streamlit, pandas, plotly, pydantic, python-dotenv, groq (unpinned, O-4) | ✅ (integrate-chaos) |
| `dashboard/app.py` | Streamlit Live / Map / Runs / Findings + STOP. Map: live Jaeger edges, accepts `from/to` and `source/target`. Findings: `reports/findings_report.md` first | ✅ v0 |
| `tools/fake_shop.py` | Fake shop (`--fail-checkout`, `/_fail?checkout=X&delay_ms=N`) | ✅ |
| `tools/check_prom.py`, `check_prom2.py`, `check_status.py` | Diagnostics (`check_status` = journey status counts, last 3 h) | ✅ |

Commands (repo folder, venv active):
```powershell
python -m src.probe                          # one probe of each journey
python -m src.observe --seconds 60           # sampler; --experiment ID, --db PATH
python -m tools.check_status                 # journey status counts (last 3 h)
python -m src.telemetry                      # Prometheus metric names + Jaeger services
python -m src.evidence --seconds 600         # evidence for the last N seconds
python -m tests.test_p2                      # unit tests
python -m src.baseline --seconds 300         # baseline (team machine only!)
python -m tests.test_runmetrics              # run-metrics tests
python -m tools.dry_run --fault-s 30         # SYNTHETIC dry run (fake shop)
streamlit run dashboard/app.py

# chaos agent (src/chaos) - no port-forward needed
python -m src.chaos.k8s_monitor                       # guardian probe + Prometheus signals + pod health
python -m src.chaos.controller --mock --skip-plan     # manifest only
python -m src.chaos.controller --preview --skip-plan  # server-side dry run, nothing created
python -m src.chaos.guardian --abort --all            # kill switch (see note below)
# NOT YET: python -m src.chaos.controller --execute    (no guardian until Stage 2)
```
Kill-switch note: `guardian --abort` without `--all` only deletes a chaos object named `reslab-experiment`, but the controller names its objects `reslab-run-<id>`. Use `--abort --all` until Stage 2 fixes the match.

### `samples` table
`samples(ts, experiment_id, journey, ok, latency_ms, status, service, metric, value)`, WAL mode.
- Journey rows: `journey`, `ok`, `latency_ms`, `status` set; `service` NULL. Every 2 s, 3 journeys in parallel.
- Service rows: `service`, `metric` (`rps` / `errors_per_s` / `p95_ms`), `value` set; `journey` NULL. Every 10 s. NaN skipped.
- Latency percentiles use successful requests only. `n == 0` means no data, not healthy.

### Confirmed telemetry facts (OTel Demo, local)
- Frontend API: `GET /api/products`, `POST /api/cart?currencyCode=USD`, `GET /api/cart?sessionId=&currencyCode=`, `POST /api/checkout?currencyCode=USD`. Product id `OLJCESPC7Z`.
- Prometheus: `traces_span_metrics_calls_total`, `traces_span_metrics_duration_milliseconds_{bucket,count,sum}`; labels `service_name`, `span_kind`, `span_name`, `status_code`, `service_criticality` (**tell P3/P5**). Rate windows ≥ `[2m]`. Idle services → NaN p95 (skipped). No error series when no errors (treat as 0).
- Jaeger v3 (D-017): `/jaeger/ui/api/v3/services` → `{"services":[...]}`; `/traces` → `{"result":{"resourceSpans":[{resource{attributes: service.name ...}, scopeSpans[{spans[{traceId, spanId, parentSpanId, name, kind, startTimeUnixNano, endTimeUnixNano, attributes, status}]}]}]}}`. Services seen: load-generator, payment, ad, shipping, frontend, frontend-web, image-provider, cart, frontend-proxy, currency, recommendation, product-catalog, flagd-ui, flagd, telemetry-docs, quote, checkout, email.
- Edges found (16): frontend-proxy→frontend; frontend→cart/checkout/product-catalog; checkout→cart/currency/email/payment/product-catalog/shipping; shipping→quote; ad/cart/payment/recommendation→flagd; load-generator→frontend-proxy. **Missing** (need P3 overrides): checkout→kafka and its consumers, cart→valkey-cart. recommendation→product-catalog not seen (low traffic). Exclude `load-generator`. flagd is a hub (infrastructure).

### Findings from local debugging (for the team, not reported numbers)
- **Checkout memory** (D-020): default 20 MiB / `GOMEMLIMIT=16MiB` → checkout at ~88% memory → GC thrashing. Signature in Jaeger: multi-second idle gaps inside `PlaceOrder`, client spans ≫ server spans (e.g. GetCart client ~900 ms vs cart server < 1 ms). After 80M / 60MiB: checkout success ~100%, no timeouts. CPU was low throughout (not CPU starvation, not Kafka, not Locust).
- **Probe timeout:** a 5 s probe timeout is reported as status 0; the order still completes server-side (seen in Jaeger).
- **Envoy `UC` 503:** rare, proxy-only (2 spans, ~9 ms) — frontend closed an idle keep-alive connection. Not our bug; no retries.
- **Own-tooling noise** (D-019): our Jaeger queries that return 404 appear as failed frontend-proxy traces → excluded in evidence. One browser request to a missing page (`/404`) also appeared; left as is.
- Latest local probe sample (laptop, reference only, not team data): 100% success on all journeys; p50 ~30 / 45 / 116 ms (browse / cart / checkout); p95 tail varies a lot run to run with 30 samples.

### Open items (in order)
1. **Stage 2 integration (Joy, D-025):** in `src/chaos/controller.py` live mode: preflight (target ready, no chaos objects, guardian baseline) → start P2 sampler in a background thread with `experiment_id = run_id` → submit → `t_inject` (Chaos Mesh injection confirmed) → `guardian.guard(...)` (kill switch on breach / max time / Ctrl+C / error) → cleanup → `t_clear` → keep sampling recovery budget + 30 s → `collect_run_metrics(run_id, targets, t_inject, t_clear)` → add to the JSON report and a `runs` row (Architecture §4.4). Targets: service → journeys map (catalog/product-catalog → browse, checkout) [verify with Jaeger]. Fix the kill-switch name match. `--force-breach` (low thresholds) for G3. Offline tests with fakes; then one live run on the local cluster. Tell P4 (safety change).
2. **Run the P2 sampler against the local cluster** (`python -m src.probe`, `python -m src.observe --seconds 60`, `python -m src.telemetry`) — not done on Kubernetes yet.
3. **`services` section of `baseline.json`** from service rows (`error_rate_mean/std`, `p95_ms`); add tests.
4. **Baseline preflight gate** for the runner: valid baseline exists, < 30 min old, shop healthy now (`latest_sli`).
5. **Docs:** README observability section (B5.1 now has endpoints + metric names).
6. **Messages:** P4 (SLO dict, helpers, `collect_evidence` signature, sustained windows, guardian wiring); P3/P5 (`samples` shape, `service_criticality`, edge format, exclude load-generator, Kafka/valkey overrides, flagd hub, O-8).
7. **On the official machine** (O-6): env URLs → probes → sampler → 5-min baseline (≥99 %) → check checkout memory limit → `stop_command` → breach test (G3) → real fixtures.

## 7. Risks specific to P2

| Risk | Mitigation |
|---|---|
| No shared team machine (O-6) | Code is environment-agnostic (env-var URLs); local Kubernetes matches P1 (D-021); Team Lead picks one official machine |
| A stale service answers on a forwarded port (happened with 9090) | Use `127.0.0.1` URLs; check the port-forward prints both IPv4 and IPv6; stop old Compose containers (D-026) |
| Collector restarts under load (liveness timeouts) lose span metrics | Avoid heavy image pulls during runs; check `kubectl -n otel-demo get pods` restarts before a baseline or run |
| Checkout memory limit makes the baseline invalid on the team machine | Check limit first; if tight, report as a risk (P3) and agree with Team Lead whether to raise it before the baseline |
| Span metric names differ / missing | Fall back to probe-only journey metrics; services via Jaeger error counts |
| Jaeger API differs on the team machine | `python -m src.telemetry`; try `/jaeger/ui/api/v3` first |
| Own tooling pollutes evidence | Excluded by URL (D-019); keep dashboard Map button use light during runs |
| Trace limit truncates evidence | Runs are short (~4 min); note appears when limit is hit |
| Probe itself adds load | 1 req / journey / 2 s max |
| Experiments overlap → polluted baseline | Capture baseline while holding the experiment lock |
