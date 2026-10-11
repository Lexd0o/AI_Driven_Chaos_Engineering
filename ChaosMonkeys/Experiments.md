# Experiments.md — Fault catalog, hypotheses, results log

All hypotheses below are **predictions to test**, not known results. Service names must be checked against `kubectl -n otel-demo get deploy` on Day 1 **[verify]**.

## 1. Actual service names (fill on Day 1)

```
(paste output of: kubectl -n otel-demo get deploy -o wide)
```

Journey → services (fill/verify with Jaeger):
| Journey | Criticality | Key services (expected) |
|---|---|---|
| checkout | 0.4 | frontend, checkout, cart, payment, shipping, currency, email, kafka |
| cart | 0.3 | frontend, cart, cart store (valkey/redis) |
| browse | 0.2 | frontend, product-catalog, currency, image-provider |
| ads/recs | 0.1 | ad, recommendation |

## 2. Fault catalog (`config/fault_catalog.yaml` mirrors this)

### Infrastructure faults (Chaos Mesh)

| ID | Fault | Target (expected) | CRD / action | Default params | Hypothesis to test | Static risk it validates |
|---|---|---|---|---|---|---|
| I1 | Pod kill | cart | PodChaos `pod-kill` | mode one | Add-to-cart fails until a new pod is Ready | single replica, no PDB |
| I2 | Network delay | product-catalog | NetworkChaos `delay` | 300 ms, 60 s | Browse p95 rises; latency cascades to frontend | high fan-in |
| I3 | Pod kill | cart store (valkey/redis) | PodChaos `pod-kill` | mode one | Cart contents lost and/or cart errors | stateful, single replica |
| I4 | CPU stress | currency or recommendation | StressChaos cpu | 2 workers, 80%, 60 s | Latency rises on pages calling it | limits / no HPA |
| I5 | Packet loss | payment | NetworkChaos `loss` | 30%, 60 s | Checkout failures; retries visible in traces | dependency on critical path |
| I6 | Partition | checkout → payment | NetworkChaos `partition` | direction to, 45 s | Checkout fails fast or hangs (timeout behaviour) | missing timeouts |

### Application faults (feature flags)

| ID | Flag | Service | What it does (per demo docs) | Hypothesis to test |
|---|---|---|---|---|
| F1 | `paymentFailure` | payment | Errors for selected % of charge calls | Checkout success ≈ 1 − failure %, no cascade to browse |
| F2 | `paymentUnreachable` | checkout | Bad address for payment service | Checkout fails; measure how fast it fails |
| F3 | `cartFailure` | cart | Selected % of EmptyCart calls hit a failing store | Post-checkout cart emptying fails |
| F4 | `productCatalogFailure` | product-catalog | Error for one specific product ID | Only that product page fails (containment) |
| F5 | `kafkaQueueProblems` | checkout, fraud-detection | Kafka overload + consumer delay → lag spike | User journey unaffected; async lag grows |
| F6 | `failedReadinessProbe` | cart | Readiness probe fails → pod NotReady | Cart unavailable while NotReady |

### Combined (Tier 2)
| ID | Faults | Hypothesis |
|---|---|---|
| C1 | `loadGeneratorVUs` ↑ + I1 | Recovery slower under load than I1 alone |

## 3. Fault classes for score categories

- **Redundancy:** I1, I3, F6
- **Dependency:** I2, I5, I6, F1, F2, F3, F4, F5
- **Capacity:** I4, C1

## 4. Run protocol (every experiment)

1. Acquire lock → check preflight (all pods Ready, baseline fresh < 30 min).
2. 60 s pre-fault observation.
3. Inject (record `t_inject` when confirmed active).
4. Observe up to `duration_s` (guardrail active).
5. Clear fault (record `t_clear`).
6. Observe recovery up to `recovery_budget_s` (default 120 s).
7. Write run record → release lock.
8. Cool-down 60 s before the next experiment.

Repeats: top-2 ranked experiments × 3; others × 1.

## 5. Results log (auto-generated table pasted here at the end)

Source: `data/runs/exp-*.json` (11 Oct 2026). Peak err = peak probe error rate during the run. Score S per experiment comes from `data/scores.json` (inconclusive runs are not scored). Label `-` = none; `fix-applied` = product-catalog liveness + readiness probes applied (Decisions.md D-020).

| Exp | Repeat | Fault / target | Label | Outcome | Peak err | Notes |
|---|---|---|---|---|---|---|
| exp-002 | r1 | cpu_stress / product-catalog | - | not_recovered | 0.75 | Guardrail abort (probe_threshold_breach); pod stuck at 32Mi limit, restarted manually |
| exp-002 | r2 | cpu_stress / product-catalog | - | degraded_recovered | 1.0 | Aborted; impact recovery ~55 s |
| exp-002 | r3 | cpu_stress / product-catalog | - | inconclusive | - | Pre-check refused (checkout slow); nothing injected |
| exp-002 | r4 | cpu_stress / product-catalog | - | not_recovered | 0.75 | Aborted; self-recovered after ~4 min, beyond the 120 s budget |
| exp-002 | r5 | cpu_stress / product-catalog | fix-applied | not_recovered | 1.0 | Pod restarted automatically by the liveness probe (no manual restart), too slow for the budget |
| exp-002 | r6 | cpu_stress / product-catalog | fix-applied | inconclusive | - | Pre-check unhealthy after the restart; nothing injected |
| exp-002 | r7 | cpu_stress / product-catalog | fix-applied | inconclusive | 1.0 | Breach ~3 s before Chaos Mesh confirmed injection -> "no fault injected" (measurement quirk) |
| exp-003 | r1 | network_delay / astronomy-db | - | degraded_recovered | 0.2 | Guardrail abort; impact recovery ~167 s |
| exp-003 | r2 | network_delay / astronomy-db | - | degraded_recovered | 0.091 | Ran to duration; impact recovery ~101 s |
| exp-003 | r3 | network_delay / astronomy-db | - | degraded_recovered | 0.083 | Ran to duration; impact recovery ~44 s |
| exp-004 | r1 | pod_kill / currency | - | survived | 0.0 | Checkout listed uncovered (run predates the checkout probe) |
| exp-006 | r1 | network_delay / shipping | - | survived | 0.0 | Checkout listed uncovered (run predates the checkout probe) |

Not run: exp-001 (pod_kill frontend-proxy; refused by design, carries the probe port-forward), exp-005 (cpu_stress cart), exp-007 (pod_kill frontend), exp-008 (cpu_stress payment; runnable since the checkout probe).

Scores (11 Oct): exp-002 S 20.6 (Capacity), exp-003 S 72.3 and exp-006 S 100 (Dependency 86.1), exp-004 S 100 (Redundancy). Headline 73.2/100, 4 of 8 scored; after-fixes overall 70.8 (exp-002 S 10.9 with one scored post-fix run).

## 6. Predicted vs confirmed (Tier 1)

| Risk ID | Predicted by audit | Experiment | Status (confirmed / refuted / inconclusive) | Evidence |
|---|---|---|---|---|
| R-product-catalog-no_liveness_probe | no liveness probe (High) | exp-002 | confirmed | Stuck at 32Mi limit, not recovered in 120 s; with the probe applied the pod restarts automatically but too slowly (D-020) |
| R-astronomy-db-no_hpa_shared_dependency | no HPA on shared dependency (Medium) | exp-003 | confirmed | degraded_recovered in 3 of 3 runs, S 72.3 |

Full list (56 predicted, 2 confirmed, 2 refuted): `data/scores.json` `findings`.
