# progress.md — Live status board

**Current phase:** Live on branch `integration`: 10 real runs across 3 fault types, scored and reported (`src.score`, `src.report`, `src.benchmark`); dashboard shows scores; STOP works. Left: P4 safety veto, merge, second-machine run, demo video, submission. Presentation 10:00.
**Last updated:** 11 Oct 2026, 07:45 IST · by: Joy (P2, integrating)
**Next gate:** CP4 / G6 freeze (T−3h) · then CP5 submit (~09:30)

Update rule: move a task when you start/finish it; add a log line; flag blockers immediately.

---

## Schedule (fill real clock times)

| Item | Relative | Clock |
|---|---|---|
| Sprint 1 start / CP0 kick-off done | D1 H0 / H0:30 | `__:__` |
| CP1 shop loads, G1 | D1 ~H2:00 | `__:__` |
| G2 | D1 ~H2:30 | `__:__` |
| CP2 = Day-1 demo + G3 | D1 H4:30 (limit H5:00) | `__:__` |
| Sprint 2 bonus window | D1 after CP2 | `__:__` |
| Sprint 3 start | D2 H0 | `__:__` |
| CP3 / Sprint 4 start | D2 H4 | `__:__` |
| G5 Tier-2 go/no-go | D2 H6 | `__:__` |
| Freeze / Sprint 5 start | T−3h | `__:__` |
| Submit target | T−30m | `__:__` |
| Hard deadline | T | `__:__` |

## Team & hierarchy

| Role | Name | Authority |
|---|---|---|
| Team Lead (extra hat) | `____` | Scope, gates, freeze, tie-breaks |
| P1 Platform / Tech Lead | `____` | Repo, contracts, VM, merges |
| P2 Observability | **Joy** | Metric definitions, data validity |
| P3 Planner | `____` | Planner logic, prompts, ranking |
| P4 Executor & Safety | `____` | **Safety veto** |
| P5 Analysis & Delivery | `____` | Scoring, report, video, form |

## Checkpoints & gates

| ID | When | Condition | Status |
|---|---|---|---|
| CP0 | D1 H0:30 | Kick-off done, schemas agreed | ⏳ |
| CP1 / G1 | D1 ~H2:00 | Shop loads; Chaos Mesh PodChaos works | ✅ PodChaos live via `src.run` (exp-004 cart pod_kill, 2 runs, 11 Oct) |
| G2 | D1 ~H2:30 | NetworkChaos delay works | ✅ 200 ms delay live (exp-003, 2 runs, degraded_recovered) |
| CP2 / G3 | D1 H4:30 (limit H5:00) | Baseline, failure observed, guardrail proven by deliberate breach, demo | ✅ G3: run-01b2e2ad3337 (`--force-breach`): breach → kill switch verified gone → row stored inconclusive (Joy's cluster, 11 Oct 03:19) |
| G4 | D2 H2 | ≥3 fault types recorded | ✅ pod_kill, network_delay, cpu_stress: 10 real runs, 7 of 8 approved experiments run (exp-001 refused by design; top-2 ×3 repeats not met, 2 repeats) |
| CP3 | D2 H4 | Report v1 generated from DB | ✅ `reports/findings_report.md` from run files + stored samples |
| G5 | D2 H6 | Tier 0 + Tier 1 complete → Tier 2 allowed | ⏳ |
| CP4 / G6 | T−3h | Freeze | ⏳ |
| CP5 | T−30m | Submitted | ⏳ |

---

## Board

### TODO (release, 07:43 IST → submit ~09:30)
- [ ] P1 + Joy · Clean run from scratch on a second machine, following the README (G6 "clean run")
- [ ] Joy · Planner text via Groq (`provider: groq` in `config/planner.yaml`); keep the deterministic plan if it fails
- [ ] Joy · Final `python -m src.report`; commit `reports/findings_report.md`
- [ ] Joy · Merge PR `integration` → main by 08:30; confirm the repo link evaluators get is public or shared
- [ ] Joy · Record the 3-minute video (Demo.md) at ~08:00 + backup take; fresh baseline < 30 min before
- [ ] Joy · Team form (team, members, tools, AI models, libraries, data); submit by ~09:30 after checking all five deliverables open

Not done, deliberately dropped (say so if asked):
- Flag-based injector (O-3) — Chaos Mesh faults only
- `bootstrap_vm.sh` / VM — local Docker Desktop Kubernetes instead (O-6)
- Top-2 experiments ×3 repeats — 2 repeats each
- Tier 2 (apply a fix, re-run) and combined failures — stretch goals, not attempted
- Dashboard Map colours by risk status, Live guardrail shading — not built

### DOING
- [ ] Joy · PR open, team message, safety self-review comment (STOP + Ctrl+C passed live 07:00) — waiting on P4 veto (deadline 07:30 passed: merge with "P4 veto pending" unless P4 objects)
- [ ] P1 · Second-machine clean run [verify]

### DONE
- [x] Joy · Evidence reads Jaeger directly (`svc/jaeger` 16686, `/jaeger/ui/api/v3`): 15 services, cart traces returned, no Jaeger restart; now the default in `config/journeys.yaml` + README — 11 Oct 07:53
- [x] Joy · Architecture overview (one page): `ChaosMonkeys/Architecture_Overview.md` — 11 Oct 07:30
- [x] Joy · Safety self-review live: STOP button (`--stop-all`) and Ctrl+C both removed the fault, cleanup confirmed; gate refused with shop unreachable — 11 Oct 07:00
- [x] Joy · Demo script with real numbers (`Demo.md`) — 11 Oct 06:45
- [x] Sprints 1–4 core: map, baseline, injection, kill switch (G3), planner + policy, 3 fault types (G4), scoring, report, benchmark, dashboard + STOP — see entries below
- [x] Problem statement analysed; plan finalised (trimmed hybrid) — 10 Oct
- [x] Roles defined; Joy = P2 — 10 Oct
- [x] Day 1 compressed to one 4–5 h sprint + bonus window; rest on Day 2 (D-014) — 10 Oct
- [x] P2 · Repo cloned (`Lexd0o/AI_Driven_Chaos_Engineering`), branch `p2-observability`, venv — 10 Oct
- [x] P2 · `telemetry.py`, `probe.py`, `observe.py`, `baseline.py`, `config/journeys.yaml`, `tools/fake_shop.py` written; tested on fake shop — 10 Oct
- [x] P2 · OTel Demo + observability stack running locally (Docker, port 8081); 3 journey probes work against real shop — 10 Oct
- [x] P2 · Prometheus span-metric names + labels confirmed; per-service sampling (rps / errors / p95) writes rows — 10 Oct
- [x] P2 · Streamlit dashboard v0 (Live / Map / Runs / Findings + STOP) — pulled forward from Day 2 — 10 Oct
- [x] P2 · Check scripts `tools/check_prom.py`, `check_prom2.py`, `check_status.py` — 10 Oct
- [x] P2 · `.gitignore` added (data/, *.db, __pycache__, .venv, .env); `__pycache__` untracked; P2 code pushed (commit 5650fa1 and later) — 10 Oct
- [x] P2 · Diagnosed slow local checkout: checkout memory limit 20 MiB + `GOMEMLIMIT=16MiB` → Go GC thrashing (Jaeger trace + docker stats). Local-only override `compose.joy-local.yaml` (80M, 60MiB) fixes it — 10 Oct
- [x] P2 · Jaeger v3 API found (`/jaeger/ui/api/v3` behind frontend-proxy); `telemetry.py` rewritten for v3 (OTLP JSON); `edges_from_traces()` returns 16 real edges — 10 Oct
- [x] P2 · Dashboard: Map draws live Jaeger edges; accepts both `from/to` and `source/target`; Findings reads `reports/findings_report.md` — 10 Oct
- [x] P2 · `src/evidence.py` `collect_evidence()`: failed + slow trace ids, error services, PromQL, quality; ignores long-lived (>60 s) traces and our own Jaeger API calls — 10 Oct
- [x] P2 · `tests/test_p2.py`: 16/16 pass (zscore, find_breach, find_healthy, build_baseline) — 10 Oct
- [x] P2 · One probe user per journey (`<probe_user>-<journey>`), no cart/checkout interference; live test 100% — 10 Oct
- [x] P2 · `src/runmetrics.py` (`make_slos`, `compute_run_metrics`, `collect_run_metrics`, CLI) + `tests/test_runmetrics.py` 8/8 + `tools/dry_run.py` (fake shop, SYNTHETIC: outcome degraded_recovered, containment 1.0) — pushed (d8063e4) — 10 Oct
- [x] Joy · Local Kubernetes: Docker Desktop kubeadm (matches P1), Helm, OTel Demo (opensearch off, collector hostMetrics off, collector 900Mi, 7 extras scaled to 0), Chaos Mesh (docker runtime, 1 controller replica). Shop 200, Jaeger traces, span metrics in Prometheus verified — 11 Oct
- [x] P3 · `discover.py` + `audit.py` on fixtures; `graph_overrides.yaml` — P3, 10 Oct
- [x] P3 · Real `graph.json` from cluster + Jaeger; full P3 pipeline live (18 nodes, 23 edges, 0 warnings; 8 exps / 3 fault types; policy 8/0) — P3, 10 Oct
- [x] P3 · LLM planner + pydantic validation + `policy.py`; deterministic fallback — P3, 10 Oct. Live LLM run waits on O-2 key (`ANTHROPIC_API_KEY`); `plan.py` falls back automatically (D-028)
- [x] P3 · Fix-as-code `fixes.py` + `config/fixes.yaml` → `reports/fixes/*.yaml` + `index.json` (60 findings, 66 patches pass `--dry-run=server`, 9 manual) — P3, 10 Oct. P5: link patches from `index.json` in the findings report
- [x] P4 · `src/run.py` + vendored injector `agents/chaos_agent_expanded` (PodChaos/NetworkChaos/StressChaos, guardian, watchdog, `--stop-all`) — 11 Oct. Mock 8/8, server dry-run 3/3 kinds, 15 unit tests. Folded into the hybrid path (D-033)
- [x] Joy · Stage 1 merge: P1's chaos_agent → `src/chaos/` (renamed modules, config/ + data/chaos/ paths, `datetime.snow` crash fixed, mock report dropped); `requirements.txt` created; `.gitignore` + P1 entries. Tests: port 7/7, guardian 12/12, P2 16/16, runmetrics 8/8; mock run OK; `k8s_monitor` probe/Prometheus/pod health OK and `controller --preview` server validation OK on Joy's cluster — 11 Oct

- [x] Joy · Stage 2 (D-027): controller ↔ guardian ↔ P2 sampler + run metrics + `runs` row; kill-switch name fix; `--force-breach`; sampler guard (refuse if another sampler writes); 39 offline tests. Live: G3 run-01b2e2ad3337 passed; run-44239ec5d896 (product-catalog 10 % loss 30 s) survived, quality ok, n=16 per journey. Pushed `integrate-chaos` 4a013bb — 11 Oct
- [x] Joy · Cluster fixed + pinned (D-034): collector logs → `[debug]` (opensearch disabled broke quote → checkout); 7 optional services disabled; `config/otel-demo-values.yaml` (helm revision 6) — 11 Oct
- [x] Joy · cart ↔ product-catalog verified in Jaeger (`GET /api/cart` calls product-catalog) → `target_journeys` product-catalog = browse, cart, checkout — 11 Oct
- [x] Joy · Integration (D-033): branch `integration` = `integrate-chaos` + P3 pipeline + hybrid run path; docs merged (P3's D-012..D-016 → D-028..D-032); tests: stage2 44, run 20, guardian 12, chaos_port 7, P2 16, runmetrics 8 — 11 Oct

- [x] Joy · Live runs on the hybrid path (11 Oct 04:30–06:20): exp-002 survived; exp-003 ×2 degraded_recovered (S 80.6 / 83.4); exp-004 ×2 degraded_recovered (S 32.66, High); exp-005 survived; exp-006 ×2 survived; exp-007 aborted by guardian (n=2, inconclusive); exp-008 survived. Overall 86.5 / 100, coverage 6 of 8
- [x] Joy · `runmetrics` fix: a breach later than t_clear + SLO window is a blip, not fault impact (`late_breach_ts`); exp-006 r1 was mislabelled — 11 Oct 04:50
- [x] Joy · Shop gate re-check (`shop_gate_with_retry`, up to 2 more 20 s windows) after three refusals from a lone fast 503 (background blip, cause unknown). Pre-injection only; needs P4 veto — 11 Oct 05:15
- [x] Joy · `src/score.py`, `src/report.py` (+ trace-ID evidence), `src/benchmark.py` + tests (101 passing); D-035 — 11 Oct 06:00
- [x] Joy · Dashboard Runs tab shows scores; STOP = `python -m src.run --stop-all`, STOP file warning in sidebar — 11 Oct 06:33
- [x] Joy · README + `Requirementsandsetup.md` steps 3–4 (install from values file, never `--reuse-values`) — 11 Oct

---

## Blockers
- P2 · No official team machine yet (O-6). Valid baseline, breach test (G3) and real fixtures need one agreed machine; Joy's and P1's laptops are both Docker Desktop Kubernetes now.
- (Safety blocker from Stage 1 resolved: the controller calls the guardian; D-027.)

## Open questions
- Joy → P1: newest code (`chaos_agent_complete`: `--live`, `--force-breach`, guard integration) was never pushed; we built Stage 1 from branch `chaos-agent` (c07af61). Push it if it still matters; we will compare and take what's missing
- Joy → P1 + Team Lead: integrator role (O-7); Joy integrates P1's agent as `src/chaos/` (D-025)
- Joy → P4: veto check on the hybrid safety path (D-033); recovery for scores = D-024
- P3 → all: frontend-proxy runs at 47–79 of 90Mi from normal traffic (tight limit); Jaeger trace cap lowered to 10000 by kubectl (not yet in `config/otel-demo-values.yaml`)
- P2 → P1: official machine; Shop / Prometheus / Jaeger URLs there (locally Jaeger = `/jaeger/ui/api/v3` behind frontend-proxy); `stop-all` command; how the stack is started
- P2 → P1/P3: check checkout memory limit + `GOMEMLIMIT` on the team machine (locally 20 MiB / 16 MiB → checkout p95 ~3.4 s, timeouts). If tight there too → predicted audit risk for P3
- P2 → P4 (to send): SLO dict + helpers + `collect_evidence()` signature; guardrail must use sustained windows (rare one-off Envoy `UC` 503s exist)
- P2 → P3/P5 (to send): `samples` shape; Prometheus label `service_criticality`; edge format `source/target/count`; exclude `load-generator`; add Kafka + valkey edges via `graph_overrides.yaml`; flagd is a hub
- O-6 (Team Lead): if there is no VM, which single machine produces the official baseline and all runs; `stop_command` for that environment
- O-1 VM provider/specs/egress · O-2 LLM provider + key · O-3 flag toggle method · O-4 version pins · O-5 remaining role names + Team Lead (see Decisions.md)

## Log
- 10 Oct 11:50 — Plan v1 finalised; markdown files created.
- 10 Oct 12:20 — Joy takes P2; 4-hour sprints adopted; role hierarchy defined; `P2_Observability.md` added.
- 10 Oct 12:50 — Day 1 compressed to a 4–5 h hard time-box (D-014); bonus window and Day 2 carry the rest.
- 10 Oct 18:15 — P2 code built and verified locally (probes, sampler, per-service Prometheus metrics, dashboard). Local checkout slow; local baseline not valid and not committed.
- 10 Oct 19:05 — Jaeger trace of slow checkout: big idle gaps inside checkout, client spans ≫ server spans. Probe 5 s timeout = source of status 0 (order still completes server-side).
- 10 Oct 19:12 — docker stats: CPU low, checkout at 17.7/20 MiB with GOMEMLIMIT 16MiB → GC thrashing. Load generator off did not help → not Locust.
- 10 Oct 19:15 — Local override (80M / 60MiB): checkout p95 3.4 s → ~0.2 s, no timeouts. Laptop usable for dev; baseline still team-machine only.
- 10 Oct 19:26 — One post-fix 503 = Envoy response flag `UC` (frontend closed idle keep-alive). Platform quirk, rare; no probe retries.
- 10 Oct 19:42 — Jaeger v3 path found; `telemetry.py` rewritten; `edges_from_traces()` → 16 edges (checkout fan-out, flagd hub; Kafka/valkey missing → overrides).
- 10 Oct 19:50 — Dashboard Map tab verified with live edges; from/to + reports/ fixes pushed.
- 10 Oct 20:45 — `evidence.py` done. Found that our own Jaeger queries (404 "No traces found") showed up as failed frontend-proxy traces → now excluded. One remaining stray = a browser 404 page, left as is.
- 10 Oct 20:47 — Unit tests 16/16. Probe users split per journey; live test 100% success.
- 10 Oct 20:51 — Re-sample: 100% success, p50 unchanged, p95 tail higher (30 samples, laptop). Not pursued.
- 10 Oct 21:30 — Handoff docs updated (progress, P2 brief, Decisions D-017–D-020 + O-6, CONTEXT).
- 10 Oct (late evening) — `collect_run_metrics()` + 8 tests + `tools/dry_run.py` pushed (d8063e4). Dry run is synthetic (fake shop), not reportable.
- 10 Oct (late evening) — P1 pushed branch `chaos-agent` (c07af61). Review: `datetime.snow` typo crashes every run; README describes `--live`/`--force-breach`/guardian integration that the committed controller lacks; kubectl probe ~0.5 s overhead; recovery = first healthy probe (not §7). P1's chat: live 10 % packet loss on product-catalog (1 replica, 30 s) → Chaos Mesh Apply/Recover OK, no failed probes (no breach).
- 10 Oct 23:13 — Joy stopped the Docker Compose demo (`docker compose down`; Prometheus/Jaeger/Grafana/OpenSearch/opamp containers were left running — found later).
- 10 Oct 23:20–23:50 — Docker Desktop Kubernetes switched kind → kubeadm (to match P1); Helm installed; OTel Demo installed (OpenSearch off).
- 11 Oct 00:00–00:30 — Pods Pending: node memory 99 % reserved → scaled 7 extras to 0. Collector failed to start (root `/` not a shared mount) → `hostMetrics` preset off. All 21 pods Running.
- 11 Oct 00:35 — Chaos Mesh installed (docker runtime, socket /var/run/docker.sock, 1 controller replica); controller, daemon, DNS Running; CRDs registered.
- 11 Oct 00:38–00:55 — Collector restarts = liveness-probe timeouts while the node was busy pulling images (not OOM); stable after a fresh pod (limit raised to 900Mi).
- 11 Oct 01:20 — Prometheus 'empty' mystery solved: leftover Compose Prometheus held port 9090 on IPv6 and answered `localhost`; cluster Prometheus at 127.0.0.1 showed span metrics (~14 calls/s background). Leftovers stopped (D-026).
- 11 Oct 01:35 — Stage 1 merge on `integrate-chaos` (see DONE). All tests pass on Joy's laptop; `--preview` validated a NetworkChaos server-side with real labels. Handoff docs + MASTER_PROMPT.md written.
- 10 Oct 22:30 — P3 live run. discover.py fixes: node name from `app.kubernetes.io/name`; Jaeger only queried for k8s workloads (querying `frontend-web` hung in-memory Jaeger → liveness restart → all traces wiped). Live flags == fixture placeholders (chart ships no probes/PDB/HPA for app services).
- 10 Oct 23:00 — P3: `fixes.py` done. Stateful single-replica, portless-consumer probes and HPA on data stores are `manual`. Patches only generated, never applied (D-007).
- 10 Oct 23:45 — P3: metrics-server installed then uninstalled (preempted flagd); probe patches use real health endpoints (66/66 pass `--dry-run=server`); rank tie-breakers (D-030) + edge `calls` (D-029); LLM planner text-only (D-028).
- 11 Oct 00:15 — P3: centrality = max(fan-in, gateway share) (D-031). frontend-proxy #1 → exp-001 = pod_kill on frontend-proxy; refused at run time because it carries the probe port-forward.
- 11 Oct 02:10 — P3 formula test: Spearman vs live traffic share 0.551 / 0.349 for D-031 (best of 3). Jaeger OOMKilled under discover's queries → queries go straight to Jaeger (`svc/jaeger` 16686), 50 traces/15 min; trace cap 25000 → 10000.
- 11 Oct 02:10–03:00 — Joy: first force-breach attempts refused by the shop gate (checkout 0 %). Root cause chain via logs + Jaeger: collector logs pipeline → missing opensearch → quote (PHP) blocks on log export → shipping 5 s timeout → checkout 500/504. Fixed with logs → `[debug]`; a `--reuse-values` upgrade brought 7 services back → collector Pending (memory) → scaled back. A stray `python -m src.observe` also ran during two refused runs (nothing injected).
- 11 Oct 03:19 — G3 passed live (run-01b2e2ad3337). 03:27 — first normal live run run-44239ec5d896: survived, quality ok.
- 11 Oct 03:47 — cart ↔ product-catalog confirmed in Jaeger.
- 11 Oct 04:00–04:20 — Integration (D-033): one hybrid run path; P3 docs merged with renumbered decisions; `integration` branch built and tested offline.
- 11 Oct 04:50 — P2: runmetrics fix (late breach = blip, not impact); exp-006 r1 re-labelled survived.
- 11 Oct 05:10 — P2/P1: hybrid run path live: pod_kill, network_delay, cpu_stress each recorded (G1, G2, G4).
- 11 Oct 05:15–05:20 — Three gate refusals from a lone fast 503 (checkout 0.89 / cart 0.89 / checkout 0.90). No cause found in Envoy/frontend logs; 200 plain GETs all 200. Gate re-check added; worked live (exp-003 r2).
- 11 Oct 06:00 — Scoring, report and ranking benchmark done. Ranked order only barely beats random (small sample); no Critical finding; CPU stress effect unverified (no CPU metric).
- 11 Oct 06:33 — Dashboard shows scores; STOP button verified (STOP file created, chaos objects removed).
- 11 Oct 06:50 — Gate refused (all journeys 0.00): nothing was forwarding the shop; another process held 8080 (401). Shop re-forwarded on 8081 (`PROBE_BASE_URL`; set `JAEGER_URL` too for evidence).
- 11 Oct 07:00 — Safety tests passed live (STOP, Ctrl+C); test runs moved to `data/safety_tests/`.
- 11 Oct 07:40 — Planner can use Groq (text only, same validation); deterministic rebuild reproduces the plan exactly.
- 11 Oct 07:53 — Direct Jaeger read verified (200, 15 services, traces) and made the default.
