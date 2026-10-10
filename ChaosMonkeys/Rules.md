# Rules.md — Team rules and rules for AI assistants

These rules are binding for every teammate **and** every AI model helping us. If a rule blocks you, raise it at stand-up; don't break it silently.

---

## 1. Safety rules (never break)

1. **Only `otel-demo` namespace may be targeted.** Never `kube-system`, `chaos-mesh`, monitoring components, or anything outside the VM.
2. **One experiment at a time.** Only `src/run.py` may inject faults, and only while holding the experiment lock. No manual `kubectl apply` of chaos during team runs (smoke tests excepted, announced in chat).
3. **Every fault has a hard cap**: Chaos Mesh specs include `duration`; flag faults are reset in a `finally` block.
4. **`make stop-all` must always work** (resets all flags, deletes all chaos CRs) and must not depend on the LLM.
5. **The LLM never executes anything.** It returns JSON only; `policy.py` validates; `inject.py` acts.
6. **No secrets in git.** API keys go in `.env` (git-ignored). Check before every push.

## 2. Scope rules

7. **Tier order is strict:** Tier 0 → Tier 1 → Tier 2. No Tier 2 work until gate G5 passes.
8. **Feature freeze at T−3h.** After that: bug fixes, docs, video only.
9. **Out of scope:** auto-healing, source-code repair, image building, second agent, genetic search.
10. **Decision gates in `Plan.md` §6 are pre-agreed.** When a gate fails, apply the fallback; don't re-debate.

## 3. Engineering rules

11. **Contracts first.** Code against the JSON contracts in `Architecture.md` §4. Changing a contract requires a `Decisions.md` entry and a heads-up in chat.
12. **Code style:** flat, simple Python functions, minimal abstraction, no class hierarchies. One module = one pipeline stage. Type hints welcome; pydantic only for validating LLM output and config.
13. **Every module is runnable on its own:** `python -m src.<module>` reads its input file(s) and writes its output file(s).
14. **Fixtures:** if your module needs cluster data, use `fixtures/` until the cluster is ready.
15. **Git:** short-lived branches per person (`p3-planner`), merge to `main` at sprint boundaries. `main` must always run `make demo`. Commit format: `<area>: <what>` (e.g. `guard: add blast-radius check`).
16. **Config over code:** thresholds, weights, targets, and catalog entries live in `config/*.yaml`, not hard-coded.
17. **Log, don't print:** every run writes to SQLite; key events also go to `data/events.log` with experiment ID.

## 4. Honesty rules (judges will check)

18. **Never invent numbers.** Any number in the report, README, slides, or video comes from a real run. Illustrative examples are labelled "illustrative".
19. **State coverage** next to every score ("7 of 9 planned experiments").
20. **Inconclusive stays inconclusive.** Missing telemetry → no score.
21. **Refuted predictions are reported**, not hidden.
22. **Disclose all AI tools and models used** (including coding assistants) in the team form.
23. **Cite related work** (ChaosEater, Krkn-AI, Gremlin, Steadybit) in the architecture page; don't claim their ideas as novel.

## 5. Process rules

24. **Stand-up at every sprint boundary** (5 min): done / next / blocked.
25. **Update `progress.md`** when you start (→ Doing) and finish (→ Done) any task.
26. **Blocked > 30 min → say so** in chat and in `progress.md` → Blockers.
27. **VM changes go through P1.** No one else installs cluster-level software without telling P1.

## 6. Rules for AI assistants (paste with CONTEXT.md)

- Read `CONTEXT.md` and `progress.md` before answering.
- Respect the frozen contracts and the tier order. If a request violates a rule above, say which rule and propose a compliant alternative.
- Prefer small, flat functions; match existing module structure in `Architecture.md` §2.
- Never generate commands that target namespaces other than `otel-demo`, or that disable safety layers.
- Mark anything you are unsure about as **[verify]** instead of stating it as fact.
- When you finish a task, output the exact lines to add to `progress.md`.
