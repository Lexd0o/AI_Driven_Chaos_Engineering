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
- **Agile (micro):** ~2-hour sprints. 5-minute stand-up at every sprint boundary: *done / next / blocked*. Kanban board lives in `progress.md`.
- **Definition of Done (any task):** runs from a script or Make target, output is saved (file or DB row), `progress.md` updated, pushed to `main` without breaking it.

---

## 4. Team roles (5 people)

| Role | Owner | Owns |
|---|---|---|
| P1 Platform | _name_ | VM, k3s, OTel Demo + Chaos Mesh install, bootstrap script, snapshots, SSH tunnels, experiment lock, README setup section |
| P2 Observability | _name_ | Prometheus + Jaeger queries, baseline, journey probe, evidence collector, trace-based edges, Streamlit dashboard (Day 2) |
| P3 Planner | _name_ | Discover, static audit, risk ranking, LLM hypotheses (validated JSON), policy validation |
| P4 Executor & Safety | _name_ | Flag injector, Chaos Mesh injector, guardrail, kill switch layers, run state machine, run records |
| P5 Analysis & Delivery | _name_ | Scoring, findings report, predicted-vs-confirmed table, benchmark, fix-as-code patches, architecture page, video, team form |

Rule: **everyone codes against the JSON contracts in `Architecture.md`** so nobody waits for the cluster. P4 records real runs into `fixtures/` early so P3/P5 can build on real data.

---

## 5. Timeline

Clock times are relative (H0 = hackathon start each day; T = submission deadline). Fill real times in `progress.md` → "Schedule".

### Day 1 — "It breaks safely"

| Sprint | Window | Goal | Owners | Exit criterion |
|---|---|---|---|---|
| S0 | H0–H1 | Kick-off: roles, repo, contracts agreed (`Architecture.md` §4), VM access for all | All | Repo exists; everyone can SSH / tunnel to VM |
| S1 | H0–H3 | k3s + OTel Demo + Chaos Mesh installed; snapshot taken | P1 | Shop loads via tunnel; one PodChaos applies & deletes |
| S1 ∥ | H1–H3 | Code against fixtures: discover/audit (P3), flag injector (P4), Prometheus/Jaeger client (P2), scoring skeleton + report template (P5) | P2–P5 | Modules run on fixture data |
| S2 | H3–H5 | Graph built; 10-min baseline captured; journey probe live; first injection (flag + pod kill) | P2, P3, P4 | `graph.json`, `baseline.json`, first run record |
| S3 | H5–H7 | Guardrail + kill switch + run state machine + experiment lock; **deliberate breach test** | P4, P2 | Logged auto-abort with reason + recovery |
| S4 | H7–H9 | End-to-end: deterministic plan → run → record → mini report; record fixtures; rehearse checkpoint demo | All | Day-1 checkpoint demo works twice in a row |

**Day-1 checkpoint demo (slide):** app running, one safe failure injected, impact visible — plus our extras: dependency map and kill switch.

### Day 2 — "It thinks, explains and ships"

| Sprint | Window | Goal | Owners | Exit criterion |
|---|---|---|---|---|
| S5 | H0–H2 | LLM planner + policy; run 6–8 experiments covering ≥3 fault types; top-2 repeated ×3 | P3, P4 | Run records in DB, ranked plan JSON with reasons |
| S6 | H1–H4 | Scoring, findings report, predicted-vs-confirmed, benchmark, fix-as-code | P5, P3 | `reports/findings_report.md` generated from DB |
| S6 ∥ | H1–H4 | Streamlit dashboard | P2 | Dashboard shows a live run + findings |
| S7 | H4–(T−3h) | Tier 2 only if gates allow (see §6) | P4, P5 | Before/after result or cleanly dropped |
| FREEZE | **T−3h** | No new features | All | — |
| S8 | T−3h → T−2h | Bug fixes, README, clean run from scratch on a second machine | P1, P2 | Teammate reproduces from README alone |
| S9 | T−2h → T−1h | Record video (≤3 min), finalise architecture page | P5, P4 | Video file + page done |
| S10 | T−1h → T−0:30 | Team form, verify all 5 deliverables open, submit | P5 + lead | Submission confirmation |

---

## 6. Decision gates (pre-agreed, no debate in the moment)

| Gate | When | Condition | If NOT met |
|---|---|---|---|
| G1 | Day 1, H2 | Chaos Mesh injects one PodChaos successfully | Use native k8s faults (delete pod / scale 0) + feature flags. Don't revisit. |
| G2 | Day 1, H3 | `NET_SCH_NETEM` available, NetworkChaos delay works | Drop network faults; use flags (`paymentUnreachable`, delays) instead |
| G3 | Day 1, end | Guardrail proven by deliberate breach | Tier 2 cancelled entirely; Day 2 starts with safety |
| G4 | Day 2, H2 | ≥3 fault types recorded | Pause Tier 1, finish Tier 0 first |
| G5 | Day 2, H4 | Tier 0 + Tier 1 complete | If not → skip Tier 2 |
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
