# Decisions.md — Decision log

Settled decisions. **Do not re-debate** without new information. To change one, add a new entry that supersedes it.

Format: ID · date · decision · why · alternatives rejected.

---

**D-001 · 10 Oct · Approach = "Trimmed hybrid" (Tier 0 → 1 → 2).**
Why: best accomplishability-to-impact ratio (team estimate ~7.5/10 for 5 people). Rejected: full hybrid (too much for 2 days), rubric-minimum only (doesn't stand out).

**D-002 · 10 Oct · Target app = OpenTelemetry Demo.**
Why: team choice; built-in traces/metrics/Grafana/Jaeger, load generator, and runtime feature-flag fault scenarios. Rejected: Online Boutique (lighter but little built-in telemetry), custom app (violates "pick from public GitHub").

**D-003 · 10 Oct · Run on one shared Linux VM with single-node k3s.**
Why: identical environment for 5 people; real kernel for network faults; lighter than nested kind. Mitigations: snapshot, bootstrap script, experiment lock.

**D-004 · 10 Oct · Two fault classes: Chaos Mesh (infra) + feature flags (app).**
Why: flags guarantee a Day-1 injection even if Chaos Mesh fights us; Chaos Mesh gives real infra faults so we are not "only simulated". Must submit with both classes.

**D-005 · 10 Oct · AI proposes → policy validates → executor acts. LLM never executes.**
Why: safety and explainability; differentiates us from ChaosEater-style manifest generation.

**D-006 · 10 Oct · Ranking and scoring are deterministic code; LLM only writes hypotheses and explanations.**
Why: repeatable, defensible numbers; works if LLM is unavailable.

**D-007 · 10 Oct · No auto-healing, no source repair, no image builds, no second agent.**
Why: not required by slides; high risk. Fixes are delivered as YAML patches (fix-as-code); one may be applied in Tier 2 replay.

**D-008 · 10 Oct · Scoring formula S = 100 × (0.45A + 0.20L + 0.20R + 0.15C) with journey criticality weights checkout 0.4 / cart 0.3 / browse 0.2 / ads-recs 0.1.**
Why: availability most user-visible; weights declared before runs; follows weighted-SLO pattern (Krkn). Coverage always shown.

**D-009 · 10 Oct · Planner benchmark validates the ranking, not the LLM.**
Why: ordering comes from deterministic ranking; honest claim.

**D-010 · 10 Oct · Python, flat functions, SQLite, Streamlit.**
Why: speed, team familiarity, simple integration.

**D-011 · 10 Oct · Feature freeze at T−3h; submit at T−30 min.**
Why: video, README, clean run need a stable system.

**D-012 · 10 Oct · [Superseded by D-014 for Day 1] Sprint length = 4 hours (5 sprints total), with a 5-min sync every 2 h.**
Why: team preference; fewer context switches. Gates unchanged except G5 moved to D2 H6. Supersedes the ~2-hour sprints in the first plan draft.

**D-013 · 10 Oct · Joy takes P2 Observability. Role hierarchy: Team Lead (extra hat) → Tech Lead/Integrator (P1) → module owners; P4 has safety veto.**
Why: P2 is on the critical path (feeds P3, P4, P5) and owns the dashboard judges see. Full brief in `P2_Observability.md`.

**D-014 · 10 Oct · Day 1 = one 4–5 h hard time-box (Sprint 1) covering the Milestone-1 checklist only; leftover Day-1 time is a bonus window pulling Day-2 work forward; everything else on Day 2 (~4 h sprints).**
Why: team wants Day 1 short and Day 2 to carry the rest. Supersedes D-012 for Day 1. Changes: G1 ~H2:00, G2 ~H2:30, G3/CP2 at H4:30 (limit H5:00); Day-1 baseline 5 min (10-min redo on Day 2); trace edges, LLM planner, scoring, dashboard, evidence collector moved to bonus window/Day 2; Day-2 gates unchanged (G4 D2 H2, G5 D2 H6, freeze T−3h).

**D-015 · 10 Oct · P2 develops against a local OTel Demo on the laptop until the team VM exists. Local data is never committed or reported.**
Why: P1's VM isn't ready yet, and the code needs a real shop to verify against. The laptop is too loaded to produce a valid baseline (checkout times out), so the baseline, fixtures and every reported number must come from the team VM (Rule 18). `data/` stays out of git.

**D-016 · 10 Oct · Per-service PromQL uses `[2m]` rate windows and SERVER spans only. Probe timeout is 5 s; status `0` means timeout.**
Why: `[1m]` returned no series (it needs 2 or more scrapes). SERVER spans count requests a service handled, without double counting client calls.

**D-017 · 10 Oct · Jaeger is read through its v3 API at `<frontend-proxy>/jaeger/ui/api/v3` (OTLP-style JSON). `edges_from_traces()` returns `{"source","target","count"}`.**
Why: the v1 paths (`/jaeger/api/services`, `/jaeger/ui/api/services`) return 404 in this demo; `/jaeger/ui/api/v3/services` returns 200. Trace params: `query.service_name`, `query.start_time_min`, `query.start_time_max` (RFC3339), `query.num_traces`. A 404 with "No traces found" means an empty result, not an error. Edge keys differ from Architecture.md §4.1 (`from`/`to`): P3 maps them in `discover.py`; the dashboard accepts both. The contract itself is unchanged.

**D-018 · 10 Oct · Each probe journey uses its own fake user: `<probe_user>-<journey>` (e.g. `probe-user-p2-checkout`).**
Why: with one shared user, the parallel checkout journey emptied the cart journey's cart, so journeys interfered and containment (C) would be unreliable. No probe retries: the probe must see what users see.

**D-019 · 10 Oct · Evidence ignores traces longer than 60 s (long-lived sessions) and traces that are our own Jaeger API calls (URL contains `/jaeger/`). `notes` = problems that lower `quality`; `info` = harmless remarks that don't.**
Why: our own Jaeger queries that return 404 appeared as failed frontend-proxy traces and would be cited as findings; a 39-minute load-generator session appeared as a "slow trace". Hitting the trace limit (1000) is a note (quality partial), because failures may be missing.

**D-020 · 10 Oct · Joy's laptop uses a local-only compose override `compose.joy-local.yaml` (checkout memory 80M, `GOMEMLIMIT=60MiB`). It is never part of the team setup and never committed.**
Why: the demo's default checkout limit (20 MiB, `GOMEMLIMIT=16MiB`) caused Go GC thrashing on the laptop (p95 3.4 s, 5 s timeouts; 0.2 s after the change). Whether the team machine has the same limit must be checked; if so, it is a real resilience risk to report (via P3's audit), not something to quietly fix before the baseline.

**D-021 · 11 Oct · Until O-6 is settled, Joy's laptop runs a local Kubernetes cluster matching P1's: Docker Desktop Kubernetes (kubeadm, context `docker-desktop`, namespace `otel-demo`), OTel Demo via Helm, Chaos Mesh via Helm with the Docker runtime.**
Why: P1's chaos agent needs Kubernetes + Chaos Mesh; matching P1's setup lets us integrate and test the same code on two machines. Docker Compose cannot run Chaos Mesh. Local settings (all needed on a 16 GB laptop; exact commands in `Requirementsandsetup.md` B11): OpenSearch off; collector `hostMetrics` preset off (Docker Desktop's root mount is not shared, the collector could not start); collector memory limit 900Mi; scaled to 0: load-generator, mcp, chatbot, agent, grafana, accounting, fraud-detection (node memory was 99 % reserved, pods stuck Pending); Chaos Mesh `controllerManager.replicaCount=1`, dashboard off. D-015 still applies: local data is never committed or reported. Does not replace D-003; O-6 still decides the official machine.

**D-022 · 11 Oct · Data layer = SQLite and JSON together (no change to Architecture.md §4).**
Why: measurements are many rows queried by time window while the sampler writes and the dashboard reads → SQLite (`samples`, `runs`, WAL). Small contract files and per-run reports stay JSON (`graph.json`, `risks.json`, `plan.json`, P1's run reports). Rejected: JSON only (P1's first slice) — no time-window queries, no concurrent reader/writer.

**D-023 · 11 Oct · Two probes, two jobs. P2's HTTP journey probes (browse/cart/checkout, `src/probe.py`) are the source of truth for scoring. P1's kubectl API-proxy probe (`src/chaos/k8s_monitor.py`) is used only by the guardian for safety.**
Why: scoring needs per-journey availability, latency and containment as users see them (30–120 ms per request); the kubectl probe adds ~0.3–0.5 s overhead and only checks product listing, but works without port-forwards, which suits a safety net. Rejected: one probe for both (would need P4's sign-off to change the guardian's input now).

**D-024 · 11 Oct · One recovery definition for scores: Architecture.md §7 (`t_healthy` = start of ≥30 s continuous healthy window in P2's samples; recovery measured from `t_breach`/`t_clear`). The guardian's own recovery check (5 healthy probes + deployment ready) only decides when cleanup is done; it never enters a score or report number.**
Why: P1's reports had `post_cleanup_probe_recovery_seconds` (first healthy probe after cleanup, e.g. 0.22 s), which is not comparable with §7 and would overstate resilience. Metric definitions are P2's (D-013).

**D-025 · 11 Oct · P1's `chaos_agent` is merged into our repo as the package `src/chaos/` (branch `integrate-chaos`); Joy integrates. P1's files are changed where needed to fit our plan, with credit in commit messages.**
Why: one codebase, one experiment id, one database. Stage 1 (done): `monitor.py` → `k8s_monitor.py`, `chaos_executor.py` → `executor.py`, `chaos_planner.py` → `planner.py`; package imports (`src.chaos.*`); inputs in `config/` (`chaos_settings.json`, `experiment.json`, `service_snapshot.json`, `app_map_overrides.json`); outputs in `data/chaos/` (reports, P1 baseline, app map, lock); fixed `datetime.snow` crash; P1's committed mock report not carried over. Stage 2 (next): guardian wired into the controller, P2 sampler + run metrics per run, `runs` row. Plan.md §4.1 named P1 as integrator → tell P1 and Team Lead (O-7). Guardian/safety changes still need P4's veto check.

**D-026 · 11 Oct · On Joy's local cluster, use port-forwards `frontend-proxy` → 8081 and `prometheus` → 9090, and env `PROBE_BASE_URL=http://localhost:8081`, `PROMETHEUS_URL=http://127.0.0.1:9090`, `JAEGER_URL=http://localhost:8081/jaeger/ui/api/v3`.**
Why: an Oracle listener holds 8080 on Joy's laptop. Leftover Docker Compose containers (prometheus, jaeger, grafana, opensearch, opamp-server — left running because `docker compose down` was run without the observability `-f` file) once held 9090 on IPv6, so `localhost:9090` silently answered from a stale Compose Prometheus (newest sample = the minute Compose was stopped) while the cluster's Prometheus was fine. `127.0.0.1` avoids that trap; stop old Compose containers before using the cluster. Never run the Compose stack and the cluster at the same time.

**D-027 · 11 Oct · Stage 2: one live run = one experiment id (the controller's run id) in reports, samples and the `runs` row.**
Why: P1's run and P2's measurement must describe the same run. Definitions: `t_inject` = Chaos Mesh confirms `Selected` + `AllInjected`; `t_clear` = the guardian's kill switch verified the object gone (else the cleanup-observed time); `target_journeys` per target in `config/chaos_settings.json` (catalog / product-catalog -> browse, cart, checkout; cart verified in Jaeger: `GET /api/cart` calls product-catalog); `runs` table per Architecture.md 4.4 with `repeat` (default 1); `--force-breach` = latency floor 1 ms and multiplier 0, and the row is stored as `inconclusive`; `--abort` matches `reslab-run-*` names and the `reslab-run-id` label; the guard baseline must be at most 30 min old; the controller refuses to start while another sampler writes to `data/runs.db` (last 15 s). Live: G3 run-01b2e2ad3337 passed; run-44239ec5d896 (product-catalog 10 % loss 30 s) survived, quality ok. Guardian changes need P4's veto check.

**D-028 · 10 Oct · LLM planner writes text only: hypothesis, reason, expected_impact.journeys.**
_(P3's D-012, renumbered at the docs merge because IDs D-012..D-016 were used twice.)_
Why: D-005/D-006 — targets, fault types, params and ranking stay deterministic; `plan.py` copies only those three fields from pydantic-validated LLM output, so the LLM can't widen blast radius even if it tries. Journeys restricted to the D-008 set. Any failure (no key, API error, refusal, invalid item) keeps the template text. Model in `config/planner.yaml` — provisional Claude (`claude-opus-5-5`) pending O-2.

**D-029 · 10 Oct · `graph.json` edges gain optional `calls` (contract §4.1, additive).**
_(P3's D-013, renumbered at the docs merge because IDs D-012..D-016 were used twice.)_
Why: count of the edge in sampled Jaeger traces (each trace counted once) — a relative traffic signal. Absent on override/fixture edges; readers must default to 0. No existing consumer breaks.

**D-030 · 10 Oct · Rank ties broken by blast radius → inbound `calls` → high-severity risks → name.**
_(P3's D-014, renumbered at the docs merge because IDs D-012..D-016 were used twice.)_
Why: tie order was arbitrary (k8s list order), which would weaken the ranked-vs-random benchmark. Blast radius = services that depend on it transitively. `calls` is a trace sample, so re-running discover can reorder services tied on everything else; a given `graph.json` always ranks the same.

**D-031 · 10 Oct · Centrality = max(normalised fan-in, gateway share) + checkout bonus (changes frozen §9 formula).**
_(P3's D-015, renumbered at the docs merge because IDs D-012..D-016 were used twice.)_
Why: fan-in only measures in-cluster callers. frontend-proxy's callers (browsers, load generator) are outside the graph, so it scored 0 (rank 16) despite fronting 100% of traffic - true since the first fixture run, unnoticed because only #1 was printed and the plan takes the top 8. Gateway share = fraction of the app unreachable from `entry_points` if the service is down. max() so nothing is double-counted; every service's score is unchanged except frontend-proxy (0 → 1.6, #1) and frontend (0.53 → 1.4, #7). Consequence: exp-001 is now pod_kill on frontend-proxy = a full outage the guardrail should abort; a valid single-point-of-failure finding. To skip it, add frontend-proxy to `forbidden_targets` in config/policy.yaml. Checkout drops to #9 and out of the top-8 plan.

**D-032 · 11 Oct · [Superseded by D-033] Executor = vendored third-party agent (`agents/chaos_agent_expanded`) driven by `src/run.py`.**
_(P3's D-016, renumbered at the docs merge because IDs D-012..D-016 were used twice.)_
Why: its Chaos Mesh lifecycle already has the safety pieces P4 needs. That means server dry-run, `can-i delete` check, healthy baseline, `AllInjected` confirmation, owned-only cleanup in `finally`, and a detached watchdog. We dropped its Groq planner (D-005), added StressChaos for `cpu_stress`, and moved its 45 s cap to `policy.yaml`. Run record (§4.4) gains additive fields: `run_id`, `fault_type`, `target`, `stop_reason`, `error`, `impact_recovery_s`, `post_fault_recovery_s`, `uncovered_journeys`, `journeys.*.samples`. Numbers are from journey GET probes, not Prometheus; checkout has no GET probe, so it is listed as uncovered. Refused in --execute: frontend-proxy (carries the probe port-forward) and checkout-only targets (payment). Run records are JSON in `data/runs/`, not SQLite yet (rule 17 open).

**D-033 · 11 Oct · Integration: one hybrid run path (supersedes D-032).** Decided by Joy (P2, integrating) to finish on time; P4 keeps the safety veto over the result.
1. `python -m src.run --exp <id>` is the only way to inject (P4): reads P3's `data/plan_validated.json`, re-checks `policy.py`, refuses the probe path (frontend-proxy) and items P2 cannot measure, writes `data/runs/<exp>-r<n>.json`, keeps `--stop-all`.
2. It calls P1's controller engine (`src/chaos/controller.py`, live-tested): inject, confirm, guardian + kill switch, cleanup. Added from P4's agent: the detached watchdog (armed before every injection; left armed if cleanup fails), `kubectl auth can-i delete` before injecting, the `data/STOP` file, `cpu_stress` (StressChaos).
3. Measurement = P2 (D-023, D-024): sampler + `runmetrics`; journeys = P3's expected journeys that P2 probes, plus Jaeger-verified ones. Checkout is now covered, so checkout-only targets (payment) are runnable.
4. Retired: the vendored agent's `lifecycle.py` and `guardian.py`, its GET probes and recovery watch; P1's Groq planner (it chose targets itself, against D-005/D-012; closes O-8).
5. Faults: pod_kill, network_delay, packet_loss (added to `policy.yaml`, max 30 %), cpu_stress. Fault durations stay at most 60 s (controller), inside policy's 120 s.
6. `agents/recovery_agent` stays in the repo for reference but is not part of the run path or the scores (D-007, D-008).
7. The opentelemetry-demo source is not committed; the demo comes from its Helm chart (D-034). P3's tracked pipeline outputs in `data/` stay as evidence; new files in `data/` are ignored.
8. The `runs` table gains `plan_experiment_id` (P3's exp-00N), added to existing databases automatically.
Why: each piece is the best tested one for its job, and every run has exactly one injector, one guardian and one measurement. Rejected: two executors (two kill switches with different limits, two different numbers for the same run).

**D-034 · 11 Oct · The cluster is defined by `config/otel-demo-values.yaml` (chart opentelemetry-demo 0.42.3). Upgrade only with `-f config/otel-demo-values.yaml`, never `--reuse-values` or `--set`.**
Why: the chart sends collector logs to opensearch even when opensearch is disabled; every log export then failed, and quote (single-threaded PHP) blocked on it, so shipping timed out and checkout failed before any chaos (found on Joy's cluster 11 Oct; the shop gate refused to inject). Values: logs pipeline exporters `[debug]`; opensearch off; collector hostMetrics off and 900Mi; accounting, agent, chatbot, fraud-detection, mcp, load-generator, grafana disabled (single-node memory). A `helm upgrade` with `--reuse-values` brought the 7 services back and the collector could not schedule (Insufficient memory). This changes the system under test, so every team cluster must use the same file. P3's Jaeger trace cap (10000, set with kubectl) is not in the file yet.

**D-035 · 11 Oct · Scoring and gate rules for the live results (src/score.py, src/report.py, src/benchmark.py).**
1. S = 100 × (0.45 A + 0.20 L + 0.20 R + 0.15 C) per run (D-008), averaged over repeats, then criticality-weighted into the overall score, always shown with coverage.
2. When every probed journey is a target, C is n/a and A, L, R are rescaled by 1/0.85 (not scored as 1.00).
3. A journey needs ≥ min_n (3) samples in the fault window or it is left out; none left → inconclusive (exp-007).
4. A breach later than t_clear + the SLO window is an unrelated blip (`late_breach_ts`), listed, not scored.
5. For timed faults R includes the fault's own 60 s; the report says so.
6. Shop gate re-checks up to 2 more times on fresh 20 s windows before refusing (a lone fast 503 about every 100 s of unknown cause). Pre-injection only; guardian, kill switch, watchdog and caps unchanged. Needs P4 veto.
7. Ranking benchmark: first confirmed and first High-or-worse position vs random orderings; exact for ≤ 8 experiments, else 1,000 seeded; small-sample caveat printed.
Why: results must be honest with 6 of 8 experiments scored. Rejected: scoring C as 1.00 (inflates S), loosening the 0.95 success threshold (hides the blip).

---

## Open decisions (resolve ASAP, then move above)

| ID | Question | Owner | Deadline |
|---|---|---|---|
| O-1 | VM provider, specs, internet egress | P1 | Day 1, H0+30m |
| O-2 | LLM provider/model and API key availability; any organiser restrictions. **Provisional:** Claude via `anthropic` SDK, model in `config/planner.yaml`; still needs a key (`ANTHROPIC_API_KEY`) and organiser OK. Without a key `plan.py` falls back to template text (D-028) | P3 | Day 1, H1 |
| O-3 | Programmatic flag toggle method + flip latency | P4 | Day 1, H3 |
| O-4 | Pinned versions (k3s, OTel chart, Python libs) | P1 | Day 1, H2 |
| O-5 | Team name + member → role mapping | All | Day 1, H0 |
| O-6 | No VM yet; P1 and Joy both run Docker Desktop Kubernetes (D-021). Which single machine produces the official baseline and all runs; `stop_command` for that environment (until then: `python -m src.chaos.guardian --abort --all` + flag reset) | Team Lead + P1 | ASAP |
| O-7 | Integrator role (Joy did the integration, D-025/D-033; ownership of `src/chaos/` after the hackathon still open): Plan.md §4.1 names P1; Joy is integrating P1's chaos agent (D-025). Confirm with P1 + Team Lead, and who owns `src/chaos/` from now on | Team Lead + P1 + Joy | Before Stage 2 is merged |
