# ChaosPilot findings report

Generated 2026-10-11 05:53 UTC by `python -m src.report` from the run files and stored samples. Every number is computed by code; the LLM did not touch the cluster or the scores.

## Summary

**Overall resilience score: 90.6 / 100** (criticality-weighted mean of the scored experiments).
**Coverage:** 2 of 8 approved experiments scored, 0 inconclusive, 6 not run.

| Exp | Fault | Target | Runs scored | Outcome(s) | S | Severity | Prediction |
|---|---|---|---|---|---|---|---|
| exp-001 | pod_kill | frontend-proxy | 0/0 | not run | n/a | not run | inconclusive |
| exp-017 | cpu_stress | frontend | 1/1 | survived | 100.0 | Low | not confirmed (no impact seen) |
| exp-018 | pod_kill | checkout | 0/0 | not run | n/a | not run | inconclusive |
| exp-016 | network_delay | astronomy-db | 1/1 | degraded_recovered | 78.1 | Medium | confirmed |
| exp-022 | network_delay | product-catalog | 0/0 | not run | n/a | not run | inconclusive |
| exp-023 | pod_kill | cart | 0/0 | not run | n/a | not run | inconclusive |
| exp-021 | cpu_stress | payment | 0/0 | not run | n/a | not run | inconclusive |
| exp-014 | cpu_stress | valkey-cart | 0/0 | not run | n/a | not run | inconclusive |

## How the score works

`S = 100 x (0.45 A + 0.20 L + 0.20 R + 0.15 C)` per run, then averaged over repeats, then weighted by service criticality (`config/criticality.yaml`).

- **A** successful requests / offered requests during the fault window; **L** requests that were ok and within the latency limit. Both are weighted across the affected journeys (checkout 0.4, cart 0.3, browse 0.2).
- **R** 1 if the shop survived; otherwise 1 minus impact recovery time over the recovery budget; 0 if it did not recover.
- **C** share of the journeys *not* targeted that stayed within SLO. When every probed journey is a target there is nothing to contain: C is shown as n/a and the other three weights are scaled to add up to 1.
- A journey with fewer samples in the fault window than its minimum is left out. If none remain, the run is **inconclusive** and gets no score.
- Severity = (100 - S) x service criticality: Critical >= 30, High >= 15, Medium >= 5, otherwise Low.

## Findings: impact seen

### astronomy-db - network_delay - Medium (S 78.1)

**Predicted:** astronomy-db: no hpa shared dependency (has_hpa=False, 2 callers - can't absorb a traffic or latency spike) - expect astronomy-db's callers to see errors or latency until the fault clears.

**Prediction status:** confirmed. Expected journeys: browse, checkout.

**Measured:**
- r1 `run-b72902c8acb3`: **degraded_recovered** - S 78.1 (A 1.00, L 0.42, R 0.48, C 1.00) - impact recovery 62 s - journeys scored: browse, checkout - numbers recomputed from samples
  - Evidence for r1 (collector quality: ok): 10 trace(s): `7f8becad203d0641456415ae2ebd5689`, `632d297c0f4216160b1aeb99d6b24a68`, `066660ce01fe93c9134a2c9868b0ab3d` (+7 more in the run file); 3 Prometheus queries recorded.

**Recommendation:**
- Suggested fix (`R-astronomy-db-no_hpa_shared_dependency`, predicted severity medium): absorb load spikes on astronomy-db
  - Manual: astronomy-db is a data store; horizontal autoscaling doesn't apply without replication.

## Tolerated faults: prediction not confirmed

The static analysis predicted a weakness here, and this fault did not expose it. That is a result of this one fault and these runs, not proof the service is safe.

### frontend - cpu_stress (S 100.0)

**Predicted:** frontend: no liveness probe (has_liveness=False - a hung (not crashed) process won't be auto-restarted) - expect frontend's callers to see errors or latency until the fault clears.

- r1 `run-8019ae253ac8`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C n/a) - journeys scored: browse, cart, checkout - C not applicable, weights rescaled - numbers recomputed from samples

- Suggested fix (`R-frontend-no_liveness_probe`, predicted severity high): add a livenessProbe (tcpSocket on port 'service') to frontend
  - `kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_liveness_probe.yaml`

## Not scored

### frontend-proxy - pod_kill (exp-001): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### checkout - pod_kill (exp-018): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### product-catalog - network_delay (exp-022): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### cart - pod_kill (exp-023): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### payment - cpu_stress (exp-021): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### valkey-cart - cpu_stress (exp-014): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).


## Ranking benchmark: ranked order vs random order

Scored experiments in the planner's order: exp-017, exp-016.

- The first confirmed finding came at position 2 in the planner's order, versus 1.5 on average over all 2 orderings of the same 2 scored experiments (1 of them hits). A random order would do this well or better 100% of the time.
- No experiment gave a High or Critical finding yet, so there is nothing to compare for it.

_Small sample: experiments scored so far, in the planner's order. This checks the ranking, not the LLM text._

## Notes and limits

- Numbers come from the runs listed above on one cluster. Repeats are shown where they exist; a single run is one observation.
- A pod kill is instant, so its fault window holds few samples; the recovery time is the more reliable number for those runs.
- For timed faults (network delay, CPU stress) the impact recovery time runs from the first breach to the start of a healthy window, so it includes the time the fault itself was active. R is therefore capped by the fault length for those runs; the recovery after the fault ended is in the run files (`post_fault_recovery_s`).
- A short unrelated failure after a fault ended is listed on the run as a blip and is not scored; the shop shows a low background error rate even with no fault running (source not yet found).
- Runs that were stopped on purpose to test the kill switch (forced breach) are not part of this report.
- Fix files in `reports/fixes/` are suggestions; nothing was applied to the cluster.
- Trace IDs open at `/jaeger/ui/trace/<id>` on the Jaeger the run used. Jaeger keeps traces in memory, so an ID stops resolving after Jaeger restarts; the IDs and queries stay in `data/runs/*.json`.
