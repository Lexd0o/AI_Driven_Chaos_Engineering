# Plan.md — ChaosPilot project plan (Trimmed Hybrid)

Status: **FINAL v1** (10 Oct 2026). Changes need an entry in `Decisions.md`.

---

## 1. The plan in plain words

Think of the shop app as a building and our system as a **fire-safety inspector**:

1. **Read the floor plan** — find every service and who talks to whom. *(Discover)*
2. **Spot obvious hazards on paper** — "only one staircase", "no fire door". *(Audit)*
3. **Decide which drill to run first** — the most dangerous-looking hazard on the most important floor. *(Rank + Plan)*
4. **Run the drill with a marshal standing by** — block the staircase, but stop instantly if people get hurt. *(Inject + Guardrail)*
5. **Time how long evacuation takes** — measure errors, slowness, and recovery time. *(Observe)*
6. **Write the inspection report** — what broke, how badly, in what order to fix it, with the exact fix. *(Score + Report)*
7. **Prove the inspector is smart** — show that its order of drills finds real problems faster than random drills. *(Benchmark)*
8. *(Stretch)* **Install the fix and re-run the drill** — show the score going up.

What makes us different from a typical team:
- We **predict** risks from configuration first, then **confirm or refute** them with real failures ("5 predicted, 3 confirmed, 1 refuted").
- We prove our AI ordering beats random selection, with numbers.
- Safety has multiple independent stop layers.
- Every fix is a ready-to-apply YAML patch, not vague advice.

---

## 2. Scope tiers (what we build, in order)

### Tier 0 — Must ship (covers the rubric and Day-1 checkpoint)
| # | Capability | Slide requirement it satisfies |
|---|---|---|
| T0.1 | OTel Demo running on k3s in VM, load generator on | Setup and app selection |
| T0.2 | Dependency graph (k8s API + traces) with manual override file | Application mapping |
| T0.3 | Baseline: error rate, p95 latency, health under steady load | Monitoring baseline |
| T0.4 | Inject faults: ≥3 types (feature flag + Chaos Mesh) | First failure injection / Multiple experiments |
| T0.5 | Guardrail + kill switch; tested by deliberate breach | Safety controls |
| T0.6 | Planner: ranked experiments with a short reason each | AI experiment planner |
| T0.7 | Recovery time + impact recorded per experiment | Observe behaviour |
| T0.8 | Findings report: ranked weaknesses + fixes + score | Analysis and scoring |
| T0.9 | 5 deliverables: repo+README, ≤3-min video, architecture page, findings report, team form | Deliverables |

### Tier 1 — Cheap differentiators (do after Tier 0 works end to end)
| # | Feature | Est. effort |
|---|---|---|
| T1.1 | Static audit → "predicted vs confirmed vs refuted" risk table | ~2 h |
| T1.2 | Planner benchmark: ranked order vs random order (offline arithmetic) | ~1–2 h |
| T1.3 | Journey-based SLIs (browse / cart / checkout) with criticality weights | ~2 h |
| T1.4 | Fix-as-code: each finding has a YAML patch (replicas, PDB, probes, HPA, limits) | ~1.5 h |
| T1.5 | Streamlit dashboard: graph, live metrics, timeline, findings, STOP button | ~4 h |

### Tier 2 — Stretch (only if the Day-1 gate is passed AND Day 2 is on schedule)
| # | Feature |
|---|---|
| T2.1 | Apply one fix + re-run the same experiment → before/after score |
| T2.2 | Chaos Mesh Workflow `StatusCheck` as an extra in-cluster abort layer |
| T2.3 | "Kill the controller" crash test: fault still auto-expires |
| T2.4 | Combined failure: load spike + pod kill |

### Explicitly out of scope
- Auto-healing / autonomous repair of the live app
- Source-code repair, building custom container images
- A second autonomous agent
- Genetic / evolutionary experiment search (mention as future work only)

---

## 3. Method: Hybrid Waterfall + Agile

- **Waterfall (macro):** two fixed gates from the organisers — Day-1 checkpoint and Day-2 submission. Phases run in order: Setup → Map → Baseline → Inject → Safety → Plan → Analyse → Polish → Submit.
- **Agile (micro):** **Day 1 = one 4–5 h hard time-boxed sprint** (Sprint 1) plus a bonus window that pulls Day-2 work forward if Sprint 1 lands early; **Day 2 = ~4 h sprints** (Sprints 3–5). Stand-up at every sprint start + a 5-minute mid-sprint sync every 2 h: *done / next / blocked*. Kanban board lives in `progress.md`.
- **Definition of Done (any task):** runs from a script or Make target, output is saved (file or DB row), `progress.md` updated, pushed to `main` without breaking it.

---

## 4. Team roles (5 people)

| Role | Owner | Owns |
|---|---|---|
| P1 Platform | _name_ | VM, k3s, OTel Demo + Chaos Mesh install, bootstrap script, snapshots, SSH tunnels, experiment lock, README setup section |
| P2 Observability | Joy | Prometheus + Jaeger queries, baseline, journey probe, evidence collector, trace-based edges, Streamlit dashboard (Day 2) — full brief in `P2_Observability.md` |
| P3 Planner | _name_ | Discover, static audit, risk ranking, LLM hypotheses (validated JSON), policy validation |
| P4 Executor & Safety | _name_ | Flag injector, Chaos Mesh injector, guardrail, kill switch layers, run state machine, run records |
| P5 Analysis & Delivery | _name_ | Scoring, findings report, predicted-vs-confirmed table, benchmark, fix-as-code patches, architecture page, video, team form |

Rule: **everyone codes against the JSON contracts in `Architecture.md`** so nobody waits for the cluster. P4 records real runs into `fixtures/` early so P3/P5 can build on real data.

### 4.1 Role hierarchy

**Authority (who decides what):**

```
                    Team Lead  (one member, extra hat — recommended: P3)
            scope · gates · freeze · tie-breaks · submission
                              │
                    Tech Lead / Integrator  (P1)
            repo & main branch · contracts · VM · merge order
                              │
   ┌──────────────┬───────────┴──────┬────────────────┬──────────────┐
P2 Observability  P3 Planner      P4 Executor&Safety  P5 Analysis&Delivery
 (data truth)    (AI decisions)   (safety veto)       (story & deliverables)
```

| Decision | Owner | Others |
|---|---|---|
| Scope, tier changes, gate outcomes, freeze | Team Lead | All consulted at stand-up |
| Contracts, repo structure, merge to `main`, VM changes | Tech Lead (P1) | Owners of affected modules |
| Safety thresholds, stopping an experiment | **P4 — veto power** | P2 supplies data |
| Metric definitions, data validity, "inconclusive" calls | P2 | P5 consulted |
| Planner logic, prompts, ranking | P3 | P5 consulted |
| Scoring formula, report wording, video, team form | P5 | Team Lead approves |

**Data dependency (who feeds whom) — the critical path:**

```
P1 Platform ──► P2 Observability ──► P3 Planner ──► P4 Executor ──► P5 Analysis
   (cluster)      (metrics, traces,     (graph, plan)    (runs)          (scores, report)
                   baseline)  └────────────────────────► P4 guardrail ──►
```
P1 and P2 are upstream of everyone: their delays cascade. Unblock them first.

**Escalation:** blocked > 30 min → tell the module owner → unresolved → Team Lead decides at the next sync.

---

## 5. Timeline

Clock times are relative (H0 = start of Day 1; D2 H0 = start of Day 2; T = submission deadline). Fill real times in `progress.md` → "Schedule".

**Structure (revised):**

| Sprint | Length | Purpose |
|---|---|---|
| **1 · Day-1 deliverables** | **4–5 h hard time-box** | Milestone 1 only — exactly the slide checklist |
| **2 · Day-1 bonus window** | Whatever Day-1 time is left after CP2 (flexible, optional) | Pull Day-2 work forward; no checkpoint of its own |
| **3 · Think & run** | D2 H0–H4 | Milestone 2 core |
| **4 · Explain & show** | D2 H4 → T−3h | Report, dashboard, Tier 1/2 |
| **5 · Release** | T−3h → T | Freeze, polish, video, submit |

### 5.1 Sprint 1 — Day-1 deliverables (4–5 h)

**Scope = the slide's Milestone 1 and nothing else:** app running · services/dependencies mapped (manual corrections allowed) · monitoring baseline · first failure injected and observed · automatic stop tested once · short demo.

**Deliberate Day-1 shortcuts (to fit 4–5 h):**
- Baseline = **5 minutes** (re-captured at 10 min on Day 2).
- Map = Kubernetes API + `graph_overrides.yaml` by hand. Trace-derived edges come later.
- First injection = **feature flag** (no dependence on Chaos Mesh); pod kill second.
- Planning = deterministic or manual for the first run. **No LLM, no dashboard, no scoring** on Day 1. Use Grafana/terminal output for the demo.

| Time | Everyone / P1 Platform | P2 Observability | P3 Planner | P4 Executor & Safety | P5 Analysis & Delivery |
|---|---|---|---|---|---|
| **0:00–0:30** | **Kick-off (CP0):** names/roles, repo, contracts frozen, VM access for all. P1: pre-flight (egress, netem, RAM) and starts k3s | Agree `samples` + `baseline.json` schemas and journey list | Agree `graph.json` + `plan.json` | Agree run-record format | Agree report skeleton |
| **0:30–2:00** | P1: k3s + Helm → OTel Demo install (image pulls) → tunnels → **shop loads (CP1 at ~2:00)** | Skeletons for `telemetry.py`, `probe.py`, `observe.py` on mocks; read demo frontend source for API routes + checkout body **[verify]**; SQLite schema; `config/journeys.yaml` | `discover.py` + `audit.py` on fixtures; hand-written `graph_overrides.yaml` | Flag injector skeleton; Chaos Mesh CR templates (pod-kill, delay, stress) | Scorer skeleton; README + architecture page skeleton |
| **2:00–2:30** | P1: Chaos Mesh install + PodChaos smoke test → **G1** | Tunnel up; manually verify the 3 probes against the live shop; fix paths | Run discover against cluster | Find programmatic flag toggle + measure flip latency (**O-3**) | Draft Day-1 demo outline |
| **2:30–3:00** | P1: NetworkChaos test → **G2**; start snapshot; `stop-all` script | Find Prometheus metric names; probe + sampler live → rows every 2 s | Real `graph.json` (k8s + overrides) | Flag injector works end to end; `stop-all` flag reset | Review `graph.json` for the demo |
| **3:00–3:15** | P1: hold the experiment lock; keep VM healthy | **5-min baseline** (load on, no faults, probe success ≥99%) → `baseline.json` | First experiment picked (manual / deterministic) | Prepare first run | — |
| **3:15–3:45** | P1: watch resources | Helpers: `latest_sli`, `zscore`, `find_breach`, `find_healthy` → P4. Watch the first injection | Plan JSON for demo | **First injection (flag)**, then **pod kill** (Chaos Mesh) | Capture screenshots/notes |
| **3:45–4:15** | P1: standby | **Deliberate breach test with P4 (G3):** guardrail aborts, recovery measured | — | Guardrail thresholds; **auto-abort proven** | Log result |
| **4:15–4:30** | Snapshot `day1-done` | Save 5 min of real samples to `fixtures/`; prep demo view | Demo: dependency map | Demo: kill switch | Demo: narrate; **CP2 sign-off** |
| **4:30–5:00** | *Buffer.* Anything not done is cut per the list below | | | | |

**Cut order if Sprint 1 slips past H4:30:**
1. Shorten baseline to 3 min (Day 2 redo).
2. Drop the Chaos Mesh pod kill; keep the flag injection + breach test.
3. Shorten the demo rehearsal to one run.

**Never cut:** shop running · a baseline · one failure observed · the breach test (slide requires auto-stop "tested at least once").

**Hard dependency:** this schedule assumes the VM is usable at H0 and the shop loads by ~2:00. If P1 is later than 2:30, P2 and P4 switch to probe-only measurement and flag-only faults, and G1/G2 slide into the bonus window.

### 5.2 Sprint 2 — Day-1 bonus window (only if CP2 passed)

Use the remaining Day-1 time, in this order. Stop when time runs out; leftovers roll to Sprint 3. Do **not** start Tier 1 or Tier 2 work.

| Order | Owner | Task |
|---|---|---|
| 1 | P1 | `bootstrap_vm.sh`, experiment lock hardening, README setup section |
| 2 | P4 | Run state machine + experiment lock in code; 2nd and 3rd fault types (delay, CPU stress, payment failure) |
| 3 | P3 | LLM access (O-2), pydantic validation, `policy.py`, planner skeleton with deterministic fallback |
| 4 | P2 | Trace-derived edges → P3; per-service Prometheus metrics in the sampler; evidence collector skeleton |
| 5 | P5 | Scoring on the first real run records; findings report v1 template filled from real data |

### 5.3 Day 2 — Sprints 3–5

| Sprint | Window | Theme | P1 Platform | P2 Observability | P3 Planner | P4 Executor & Safety | P5 Analysis & Delivery | Checkpoint |
|---|---|---|---|---|---|---|---|---|
| **3** | D2 H0–H4 | Think & run | Keep VM healthy; README setup | Fresh 10-min baseline; evidence collector; per-run A/L/containment; quality flag; dashboard skeleton | LLM planner + validation + policy; fallback | Run 6–8 experiments, ≥3 types, top-2 ×3; **G4** (H2) | Scoring on real data; findings report v1; fix-as-code | **CP3:** G4 + report v1 from DB |
| **4** | D2 H4 → T−3h | Explain & show | Clean-run rehearsal | Dashboard Live/Map/Runs/Findings + STOP | Predicted-vs-confirmed statuses; benchmark with P5 | Tier 2 only if **G5** (D2 H6): fix + replay, StatusCheck | Predicted-vs-confirmed table; benchmark; **backup video** | **CP4:** G5 decided; **FREEZE at T−3h** |
| **5** | T−3h → T | Release | Clean run from scratch on second machine | Dashboard bug fixes; README section; video-ready screens | Prompt/plan freeze; README section | Final run for video | Final video ≤3 min; architecture page; team form; submit T−30m | **CP5:** submitted |

(Anything rolled over from Sprint 2 is finished at the start of Sprint 3 before new work begins.)

### 5.4 Checkpoints

| ID | When | Condition |
|---|---|---|
| CP0 | D1 H0:30 | Kick-off done: names, repo, contracts frozen, VM access for all |
| CP1 | D1 ~H2:00 | Shop loads via tunnel; Chaos Mesh smoke test (G1) |
| CP2 | D1 H4:30 (limit H5:00) | **Day-1 deliverables complete:** map, baseline, first failure observed, auto-stop proven (G3), demo works |
| — | D1 bonus window | No checkpoint; log what was pulled forward |
| CP3 | D2 H4 | G4 passed + findings report v1 generated from DB |
| CP4 | D2 H6 → T−3h | G5 decided; feature freeze at T−3h |
| CP5 | T−30m | Submitted |

**Day-1 checkpoint demo (slide):** app running, one safe failure injected, impact visible — plus our extras: dependency map and kill switch.

---

## 6. Decision gates (pre-agreed, no debate in the moment)

| Gate | When | Condition | If NOT met |
|---|---|---|---|
| G1 | Day 1, ~H2:00 | Chaos Mesh injects one PodChaos successfully | Use native k8s faults (delete pod / scale 0) + feature flags. Don't revisit. |
| G2 | Day 1, ~H2:30 | `NET_SCH_NETEM` available, NetworkChaos delay works | Drop network faults; use flags (`paymentUnreachable`, delays) instead |
| G3 | Day 1, H4:30 (limit H5:00) | Guardrail proven by deliberate breach | Tier 2 cancelled entirely; Day 2 starts with safety |
| G4 | Day 2, H2 | ≥3 fault types recorded | Pause Tier 1, finish Tier 0 first |
| G5 | Day 2, H6 (mid-Sprint 4) | Tier 0 + Tier 1 complete | If not → skip Tier 2 |
| G6 | T−3h | Freeze | Unfinished work is cut, not rushed |
| G7 | Any time | VM dead / unreachable > 20 min | Restore snapshot or re-run bootstrap script; one person only |

---

## 7. Definition of Done per milestone

**Day 1 done when:**
- [ ] Shop running end to end in the sandbox
- [ ] Services, dependencies, data flows mapped (with manual corrections file)
- [ ] Baseline error rate, latency, health captured under steady load
- [ ] ≥1 failure triggered and observed
- [ ] Auto-stop/rollback tested at least once
- [ ] Short demo rehearsed

**Day 2 done when:**
- [ ] AI ranks and selects experiments using the dependency map, with a reason each
- [ ] ≥3 failure types run; errors, slowdowns, recovery time recorded
- [ ] AI explains findings, ranks weaknesses by severity, recommends fixes, gives a resilience score
- [ ] Feature freeze respected; README final; clean run from scratch
- [ ] Demo video ≤3 min; architecture overview; findings report
- [ ] Team form; all 5 deliverables verified; submitted ~30 min early

---

## 8. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | VM not ready / no internet egress / can't pull images | Med | Critical | Check egress first hour; pre-pull images; snapshot after healthy install | P1 |
| R2 | OTel Demo too heavy for VM, pods OOM | Med | High | Size VM ≥16 GB; disable optional components if needed; reduce load-gen users | P1 |
| R3 | Chaos Mesh network faults fail (kernel/runtime) | Med | Med | Gates G1/G2; flags as fallback | P1, P4 |
| R4 | Two people run experiments at once → garbage data | High | High | Experiment lock; only runner script may inject | P4 |
| R5 | Feature-flag change takes long to apply → weak kill switch | Med | Med | Measure flip latency on Day 1; rely on Chaos Mesh deletion + duration cap for infra faults | P4 |
| R6 | LLM API down / slow / no key | Med | Med | Deterministic planner fallback; cache last good plan | P3 |
| R7 | Integration collisions at the end | High | High | Frozen JSON contracts; fixtures; integrate daily at S4 | All |
| R8 | Fix + replay shows no improvement | Med | Low | Pair fix with matching experiment; report honestly if flat | P5 |
| R9 | Noisy single runs | High | Med | Fixed workload; top-2 experiments ×3, report median + range; coverage line | P4, P5 |
| R10 | Demo/video squeezed | High | High | Freeze at T−3h; record a backup video as soon as Day-2 core works | P5 |

---

## 9. Deliverables mapping

| Deliverable | Source in repo | Owner |
|---|---|---|
| 1. Source code + README | repo root, `README.md` | P1 |
| 2. Demo video ≤3 min | `Demo.md` script | P5 |
| 3. Architecture overview (1 page) | `Architecture.md` → exported page/slide | P5 |
| 4. Findings report | `reports/findings_report.md` (generated) | P5 |
| 5. Team form | tools, AI models, libraries, data used → from `techstack.md` | P5 |
