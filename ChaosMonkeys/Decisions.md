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

**D-012 · 10 Oct · Sprint length = 4 hours (5 sprints total), with a 5-min sync every 2 h.**
Why: team preference; fewer context switches. Gates unchanged except G5 moved to D2 H6. Supersedes the ~2-hour sprints in the first plan draft.

**D-013 · 10 Oct · Joy takes P2 Observability. Role hierarchy: Team Lead (extra hat) → Tech Lead/Integrator (P1) → module owners; P4 has safety veto.**
Why: P2 is on the critical path (feeds P3, P4, P5) and owns the dashboard judges see. Full brief in `P2_Observability.md`.

---

## Open decisions (resolve ASAP, then move above)

| ID | Question | Owner | Deadline |
|---|---|---|---|
| O-1 | VM provider, specs, internet egress | P1 | Day 1, H0+30m |
| O-2 | LLM provider/model and API key availability; any organiser restrictions | P3 | Day 1, H1 |
| O-3 | Programmatic flag toggle method + flip latency | P4 | Day 1, H3 |
| O-4 | Pinned versions (k3s, OTel chart, Python libs) | P1 | Day 1, H2 |
| O-5 | Team name + member → role mapping | All | Day 1, H0 |
