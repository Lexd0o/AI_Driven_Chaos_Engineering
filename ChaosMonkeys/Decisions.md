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

**D-012 · 10 Oct · LLM planner writes text only: hypothesis, reason, expected_impact.journeys.**
Why: D-005/D-006 — targets, fault types, params and ranking stay deterministic; `plan.py` copies only those three fields from pydantic-validated LLM output, so the LLM can't widen blast radius even if it tries. Journeys restricted to the D-008 set. Any failure (no key, API error, refusal, invalid item) keeps the template text. Model in `config/planner.yaml` — provisional Claude (`claude-opus-5-5`) pending O-2.

**D-013 · 10 Oct · `graph.json` edges gain optional `calls` (contract §4.1, additive).**
Why: count of the edge in sampled Jaeger traces (each trace counted once) — a relative traffic signal. Absent on override/fixture edges; readers must default to 0. No existing consumer breaks.

**D-014 · 10 Oct · Rank ties broken by blast radius → inbound `calls` → high-severity risks → name.**
Why: tie order was arbitrary (k8s list order), which would weaken the ranked-vs-random benchmark. Blast radius = services that depend on it transitively. `calls` is a trace sample, so re-running discover can reorder services tied on everything else; a given `graph.json` always ranks the same.

**D-015 · 10 Oct · Centrality = max(normalised fan-in, gateway share) + checkout bonus (changes frozen §9 formula).**
Why: fan-in only measures in-cluster callers. frontend-proxy's callers (browsers, load generator) are outside the graph, so it scored 0 (rank 16) despite fronting 100% of traffic - true since the first fixture run, unnoticed because only #1 was printed and the plan takes the top 8. Gateway share = fraction of the app unreachable from `entry_points` if the service is down. max() so nothing is double-counted; every service's score is unchanged except frontend-proxy (0 → 1.6, #1) and frontend (0.53 → 1.4, #7). Consequence: exp-001 is now pod_kill on frontend-proxy = a full outage the guardrail should abort; a valid single-point-of-failure finding. To skip it, add frontend-proxy to `forbidden_targets` in config/policy.yaml. Checkout drops to #9 and out of the top-8 plan.
Update 11 Oct: exp-001 is not run as a guardrail demo - `run.py --execute` refuses it because frontend-proxy is in `probe_path_services` (carries the probe port-forward; see D-016). It stays in the plan as a predicted single-point-of-failure finding.

**D-016 · 11 Oct · Executor = vendored third-party agent (`agents/chaos_agent_expanded`) driven by `src/run.py`.**
Why: its Chaos Mesh lifecycle already has the safety pieces P4 needs. That means server dry-run, `can-i delete` check, healthy baseline, `AllInjected` confirmation, owned-only cleanup in `finally`, and a detached watchdog. We dropped its Groq planner (D-005), added StressChaos for `cpu_stress`, and moved its 45 s cap to `policy.yaml`. Run record (§4.4) gains additive fields: `run_id`, `fault_type`, `target`, `stop_reason`, `error`, `impact_recovery_s`, `post_fault_recovery_s`, `uncovered_journeys`, `journeys.*.samples`. Numbers are from journey GET probes, not Prometheus; checkout has no GET probe, so it is listed as uncovered. Refused in --execute: frontend-proxy (carries the probe port-forward) and checkout-only targets (payment). Run records are JSON in `data/runs/`, not SQLite yet (rule 17 open).

**D-017 · 11 Oct · Scoring and analytics choices (`src/score.py`, `src/report.py`, `config/scoring.yaml`).**
`score.py` implements D-008 with these precisions, where the spec left room:
- Containment C is undefined when no non-target journey was measured; the weights are then renormalised over A/L/R (never C = 1 by default).
- Per experiment: S = mean over scored repeats; R and C are taken from the worst scored repeat. Experiment weight = the largest journey weight among its expected journeys. Overall = sum(weight x S) / sum(weight).
- Probe `shipping-quote` counts as journey `cart` (`journey_aliases`); when two probes map to one journey, the worst A and L are kept.
- Inconclusive repeats are excluded from S but counted in coverage ("N of M planned experiments scored (k inconclusive)").
- No runs -> `resilience_score` is null and `score_status` is "not_scored". It is never estimated.
- Findings rank: confirmed first (by severity value (100-S) x criticality), then predicted/inconclusive (audit severity, criticality, rank), then refuted.
- `report.py`: the AI writes prose only. Any text containing a number not present in the facts it was given (or outside the length bounds, or with an unknown/duplicate risk_id) is discarded and the template text used (Rules.md 5, 18).
Why: keeps scores honest and reproducible (D-006) and the report complete with no LLM. Alternatives rejected: treating missing containment as 1 (inflates scores), imputing a score before any run.

---

## Open decisions (resolve ASAP, then move above)

| ID | Question | Owner | Deadline |
|---|---|---|---|
| O-1 | VM provider, specs, internet egress | P1 | Day 1, H0+30m |
| O-2 | LLM provider/model and API key availability; any organiser restrictions. **Provisional:** Claude via `anthropic` SDK, model in `config/planner.yaml`; still needs a key (`ANTHROPIC_API_KEY`) and organiser OK | P3 | Day 1, H1 |
| O-3 | Programmatic flag toggle method + flip latency | P4 | Day 1, H3 |
| O-4 | Pinned versions (k3s, OTel chart, Python libs) | P1 | Day 1, H2 |
| O-5 | Team name + member → role mapping | All | Day 1, H0 |
