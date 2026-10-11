# Recovery & Solutions report: run-dd9f4e6c53cf

**Verdict:** CONFIRMED  |  **Narrative source:** llm

**Experiment:** network_delay on `astronomy-db`, stop reason `completed`

The experiment confirmed that a 200 ms network delay caused a high‑severity latency inflation, with the fault p95 rising to 1730.1 ms from a baseline of 141.9 ms, yet no errors were observed and recovery was immediate. The overall score was 85.0, with the latency inflation component contributing a penalty of 15.0.

## Verification

| Check | Passed | Detail |
|---|---|---|
| live_execute_run | True | mode=execute |
| controller_confirmed_injection | True | fault_injected=True, fault_confirmed_at=2026-10-11T09:16:08.678541+00:00, event=True |
| timeline_consistent | True | submitting=2026-10-11T09:16:07.208167+00:00, confirmed=2026-10-11T09:16:08.678541+00:00, cleanup=2026-10-11T09:17:15.811413+00:00 |
| manifest_matches_plan | True | manifest matches plan |
| probe_effect_observed | True | latency_effect=True, error_effect=False |

## Windows

| Window | n | success | p50 ms | p95 ms | errors |
|---|---|---|---|---|---|
| baseline | 32 | 1.0 | 57.3 | 141.9 | {} |
| fault | 60 | 1.0 | 471.7 | 1730.1 | {} |
| recovery | 150 | 1.0 | 60.1 | 132.6 | {} |

## Resilience score: 85.0

| Component | Weight | Normalized | Penalty |
|---|---|---|---|
| error_rate | 35 | 0.0 | 0.0 |
| slo_breach | 20 | 0.0 | 0.0 |
| slow_recovery | 20 | 0.0 | 0.0 |
| latency_inflation | 15 | 1.0 | 15.0 |
| single_replica | 10 | 0.0 | 0.0 |

## Findings (ranked)

- **[high] Latency inflated versus baseline** (-15.0): During the fault window the p95 latency increased from 141.9 ms to 1730.1 ms, a ratio of 12.19 and a delta of 1588.2 ms, while the injected latency was 200 ms. The amplification factor of 2.07 indicates that the observed latency was more than twice the injected delay, confirming significant latency inflation.

## Recovery

- applicable: True, recovered: True, seconds: 0.0
- reset at: 2026-10-11T09:17:16.056863+00:00, recovered at: 2026-10-11T09:17:16.056863+00:00
- controller-reported seconds: 8.6
- time to first impact: 0.1 s, fault active: 67.13 s

## Recommendations (proposed, not applied or verified)

- `latency_inflation` -> **circuit_breaker**: The measured amplification and high p95 latency suggest that a circuit breaker could prevent cascading delays by short‑circuiting calls when latency exceeds acceptable thresholds.
- `latency_inflation` -> **set_timeouts**: Setting shorter timeouts would limit the impact of inflated latency on callers, ensuring they fail fast rather than waiting for the extended response times.

## Limitations

- Evidence is one HTTP business probe (sequential, ~poll interval); no per-service metrics or traces.
- Latency percentiles use successful probes only; with few samples p95 is close to the maximum.
- Network faults hit one whole pod, not a single dependency edge.
- Single run: the score is heuristic and applies to this experiment only.
- Recommendations are proposals; none were applied or verified.

## Concrete patches for this service (generated, not applied)

- no generated patch matches these actions (the actions are application-level changes)
- Apply through the dashboard Control tab: dry-run, type `apply-fix`, roll back if needed.
