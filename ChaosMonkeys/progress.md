# progress.md — Live status board

**Current phase:** Day 2 · P3 pipeline + scoring/report built and verified live (discover → audit → rank → plan → policy → fixes → run → score → report); 0 experiments executed yet
**Last updated:** 11 Oct 2026 · by: P3 (docs refresh)
**Next gate:** G4 = ≥ 3 fault types RECORDED, i.e. actually run experiments (`python -m src.run --exp <id> --execute`)

Update rule: move a task when you start/finish it; add a log line; flag blockers immediately.

---

## Schedule (fill real clock times)

| Item | Time |
|---|---|
| Day 1 start (H0) | `__:__` |
| Day 1 checkpoint demo | `__:__` |
| Day 1 end | `__:__` |
| Day 2 start | `__:__` |
| Feature freeze (T−3h) | `__:__` |
| Submit target (T−30m) | `__:__` |
| Hard deadline (T) | `__:__` |

## Team

| Role | Name |
|---|---|
| P1 Platform | `____` |
| P2 Observability | `____` |
| P3 Planner | `____` |
| P4 Executor & Safety | `____` |
| P5 Analysis & Delivery | `____` |

## Gates

| Gate | Condition | Status |
|---|---|---|
| G1 | Chaos Mesh PodChaos works (D1 H2) | ⏳ |
| G2 | NetworkChaos delay works (D1 H3) | ⏳ |
| G3 | Guardrail proven by deliberate breach (D1 end) | ⏳ |
| G4 | ≥3 fault types recorded (D2 H2) | ⏳ |
| G5 | Tier 0 + Tier 1 complete (D2 H4) | ⏳ |
| G6 | Freeze (T−3h) | ⏳ |

---

## Board

### TODO

**Day 1**
- [ ] S0 · All · Assign names to roles; create repo; agree contracts (Architecture.md §4)
- [ ] S0 · P1 · VM access for all 5; pre-flight checks (Requirementsandsetup.md B2)
- [ ] S1 · P1 · Install k3s + Helm; install OTel Demo (pinned); tunnel works
- [ ] S1 · P1 · Install Chaos Mesh; PodChaos smoke test (G1); NetworkChaos test (G2); snapshot
- [ ] S1 · P1 · `scripts/bootstrap_vm.sh` (stop-all is `python -m src.run --stop-all`; no Makefile)
- [ ] S1 · P2 · Prometheus + Jaeger clients; find span-metric names **[verify]**
- [ ] S1 · P4 · Flag injector; find programmatic toggle + flip latency (O-3)
- [ ] S1 · P5 · architecture page draft
- [ ] S2 · P2 · 10-min baseline → `baseline.json` (journey GET probes exist in `config/executor.yaml`; checkout has none)
- [ ] S2 · P4 · First live injection (injector built, not yet executed live)
- [ ] S3 · P4+P2 · Deliberate breach test (G3)
- [ ] S4 · All · End-to-end: deterministic plan → run → record → mini report; record fixtures
- [ ] S4 · All · Rehearse Day-1 checkpoint demo (twice)

**Day 2**
- [ ] S5 · P4 · Run 6–8 experiments (≥3 types); top-2 × 3 repeats (G4)
- [ ] S6 · P5 · Planner benchmark
- [ ] S6 · P2 · Streamlit dashboard with STOP button
- [ ] S6 · P5 · Record backup video once core works
- [ ] S7 · P4+P5 · (Tier 2, only if G5) apply fix + replay; StatusCheck; crash test
- [ ] S8 · P1+P2 · Freeze; bug fixes; README; clean run on second machine
- [ ] S9 · P5+P4 · Record final video ≤3 min; finalise architecture page
- [ ] S10 · P5 · Team form; verify 5 deliverables; submit

### DOING
- (none)

### DONE
- [x] S6 · P3 (for P5's task) · Scoring `src/score.py` (D-008 + D-017, `config/scoring.yaml`) → `data/scores.json`; findings report `src/report.py` → `reports/findings_report.md/.json` with AI explanations behind a number guard and template fallback - 11 Oct. Works with 0 runs ("not scored", coverage 0 of 8). Tests: `test_score.py`, `test_report.py`. P5: review/extend rather than rebuild.
- [x] Review fixes - 11 Oct: journey mapping in `plan.py` (`journey_roots` in `config/planner.yaml`); LLM fallback via shared `src/llm.py`; `discover.py` refuses an empty live graph (`--allow-no-traces` overrides); policy hardening (fails closed without `forbidden_targets`, numeric param checks); `rank.py` handles an empty graph. 52 unit tests pass (`python -m unittest discover -s tests`).
- [x] S2/S3 · P4 · experiment lock (`data/run.lock`), `data/STOP`, guardrail/state machine in `src/run.py` + vendored agent (see first run.py entry below).
- [x] S2/S3 · P4 · `src/run.py` + vendored injector `agents/chaos_agent_expanded` (PodChaos/NetworkChaos/StressChaos, guardian, watchdog, recovery watch, `--stop-all`) - 11 Oct. Mock 8/8, server dry-run 3/3 kinds, 15 unit tests pass. **No live injection yet**; G1/G2/G3 still open.
- [x] S5 · P3 · LLM planner + pydantic validation + `policy.py`; deterministic fallback — P3, 10 Oct. Code done and offline-tested; **live LLM run waits on O-2 key** (`ANTHROPIC_API_KEY`), until then `plan.py` falls back automatically.
- [x] S6 · P3 · Fix-as-code `fixes.py` + `config/fixes.yaml` → `reports/fixes/*.yaml` + `index.json` (60 findings, 66 patches all pass `--dry-run=server`, 9 manual) — P3, 10 Oct. P5: link patches from `index.json` in the findings report.
- [x] S1 · P3 · `discover.py` + `audit.py` on fixtures; `graph_overrides.yaml` — P3, 10 Oct
- [x] S2 · P3 · Real `graph.json` from cluster + Jaeger; full P3 pipeline live (18 nodes, 23 edges, 0 warnings; 8 exps / 3 fault types; policy 8/0) — P3, 10 Oct
- [x] Problem statement analysed; plan finalised (trimmed hybrid) — Joy, 10 Oct

---

## Blockers
- (none)

## Closed
- Jaeger OOMKilled at its 600Mi limit (MEMORY_MAX_TRACES=25000): trace cap lowered to 10000, applied 11 Oct. Undo: `kubectl -n otel-demo rollout undo deploy/jaeger`.

## Open questions
- O-1 VM provider/specs/egress · O-2 LLM provider + key · O-3 flag toggle method · O-4 version pins · O-5 role names (see Decisions.md)

## Log
- 10 Oct 11:50 — Plan v1 finalised; markdown files created.
- 10 Oct 22:30 — P3 live run. discover.py fixes: node name from `app.kubernetes.io/name` (component label = `all-in-one`/`server` for jaeger/prometheus, dodged ignore/forbidden lists); Jaeger only queried for k8s workloads (querying `frontend-web` hung in-memory Jaeger -> liveness restart -> all traces wiped). Finding: live flags == fixture placeholders (chart ships no probes/PDB/HPA for app services), so risks.json is identical to the fixture run.
- 10 Oct 23:00 — `fixes.py` done. Stateful single-replica, portless-consumer probes (accounting, fraud-detection) and HPA on data stores are `manual` (no honest generic patch). HPA patches need metrics-server (not on Docker Desktop by default). Patches only generated, never applied (D-007).
- 10 Oct 23:45 — P3: (1) metrics-server installed then **uninstalled** - see 00:40 entry. Node at 84% memory - apply fixes one at a time in Tier 2. (2) Probe patches now use real health endpoints (gRPC health on 7 services, HTTP on shipping/image-provider, exec on valkey/postgres) - all verified live; 66/66 pass `--dry-run=server`. (3) rank.py tie-breakers (D-014) + edge `calls` (D-013); only accounting/fraud-detection (#17-18, score 0) still tie. (4) LLM planner in plan.py (D-012), provisional Claude; falls back cleanly with no key / bad key. Note: frontend-proxy scores 0 (fan-in 0) despite fronting all traffic - formula artifact, discuss before benchmark.
- 11 Oct 00:15 — P3: §9 formula changed (D-015): centrality = max(fan-in, gateway share). frontend-proxy 0 → 1.6 (#1), frontend #9 → #7. exp-001 is now pod_kill on frontend-proxy (full outage, guardrail should abort) - team: keep, or add to policy forbidden_targets. Checkout left the top-8 plan. **Heads-up to all: frozen formula changed.**
- 11 Oct 00:40 — P3 live retest, 29/29 checks pass (discover→fixes). Found + fixed: (a) metrics-server (system-cluster-critical) **preempted flagd**, which then couldn't reschedule (node 95% of memory requested, flagd needs 506Mi) - uninstalled it, flagd back 2/2. HPA patches need metrics-server, which only fits if ~200Mi of requests is freed (e.g. grafana's 1Gi). (b) plan.py `expected_impact.journeys` held service names, not D-008 journeys - now derived from `journey_roots` in config/planner.yaml + the graph (replace with P2's T1.3 map). Jaeger OOMKilled at its 600Mi limit after ~74 min (MEMORY_MAX_TRACES=25000) - restarts wipe all traces (closed 11 Oct, see 02:10 entry).
- 11 Oct 02:10 — P3 live formula test (no faults injected): ground truth = share of real user requests (traces entering frontend-proxy) touching each service. Spearman vs live: C max(fan-in, gateway) [current, D-015] 0.551 / 0.349 (two samples) > B fan-in+fan-out+forward reach 0.460 / 0.235 > A fan-in+fan-out 0.263 / 0.043; top-5 overlap 3/5 vs 2/5 vs 2/5. Keep D-015. Incidents: Jaeger OOMKilled again (20:33 UTC) under discover's 200-trace/1h queries while already near its 600Mi cap; frontend-proxy hit 88.6/90Mi while proxying Jaeger queries (0 restarts). Fixed in discover.py: queries go straight to Jaeger (`kubectl -n otel-demo port-forward svc/jaeger 16686:16686`, new default JAEGER_URL), 50 traces/15m, pause between queries, connection errors -> WARNING. Re-run: same 17 edges, 0 warnings, Jaeger +32MiB, proxy flat. Jaeger trace cap lowered 25000 -> 10000 (approved by user, applied 11 Oct; undo: `kubectl -n otel-demo rollout undo deploy/jaeger`) - fixes the hourly OOM. frontend-proxy runs at 47-79 of 90Mi from normal traffic - limit is tight.
- 11 Oct (later) — P3 docs + analytics pass: `score.py` and `report.py` built and tested (D-017); review fixes applied (journey mapping, `src/llm.py` fallback, discover refuses empty live graph, policy hardening, rank empty graph); 52 unit tests pass. `run.py` refuses exp-001 (pod_kill frontend-proxy) via `probe_path_services` (D-016), so D-015's "guardrail should abort" no longer applies. README.md written. Still 0 experiments run, so the report is "not scored". Next: G4.
