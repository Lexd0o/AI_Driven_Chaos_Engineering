# ChaosPilot findings report

Snapshot: scores generated 2026-10-11T00:47:00.324902+00:00, graph generated 2026-10-11T00:46:29.523548+00:00

## Resilience score

**Not scored yet - 0 of 8 planned experiments run**

No experiments have run yet, so no resilience score exists; findings are predicted from the static audit only.

Next step:

```
python -m src.run --list      # shows which approved experiments are runnable
python -m src.run --exp exp-002 --preview   # then --execute (product-catalog, cpu_stress)
python -m src.run --exp exp-003 --preview   # then --execute (astronomy-db, network_delay)
python -m src.run --exp exp-004 --preview   # then --execute (currency, pod_kill)
python -m src.score && python -m src.report
```

Runnable now: exp-002, exp-003, exp-004, exp-005, exp-006, exp-007. Experiments on the probe path (frontend-proxy) or with only a checkout journey (no GET probe) are refused by `src.run`.

## Executive summary

_Template_

Not scored yet: 0 of 8 planned experiments have been run. Findings: 0 confirmed, 0 refuted, 0 inconclusive, 60 predicted (untested). Highest-ranked weakness: frontend-proxy - no liveness probe (High).

## Weaknesses ranked by severity

| Rank | Severity | Status | Service | Weakness | Fix |
|---|---|---|---|---|---|
| 1 | High | predicted | frontend-proxy | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend-proxy |
| 2 | High | predicted | frontend-proxy | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend-proxy |
| 3 | High | predicted | frontend-proxy | single replica no pdb | scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1) |
| 4 | High | predicted | frontend | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend |
| 5 | High | predicted | frontend | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend |
| 6 | High | predicted | frontend | single replica no pdb | scale frontend to 2 replicas and add a PDB (minAvailable=1) |
| 7 | High | predicted | payment | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to payment |
| 8 | High | predicted | payment | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to payment |
| 9 | High | predicted | payment | single replica no pdb | scale payment to 2 replicas and add a PDB (minAvailable=1) |
| 10 | High | predicted | checkout | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to checkout |
| 11 | High | predicted | checkout | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to checkout |
| 12 | High | predicted | checkout | single replica no pdb | scale checkout to 2 replicas and add a PDB (minAvailable=1) |
| 13 | High | predicted | product-catalog | single replica no pdb | scale product-catalog to 2 replicas and add a PDB (minAvailable=1) |
| 14 | High | predicted | astronomy-db | stateful single replica | make astronomy-db highly available |
| 15 | High | predicted | currency | single replica no pdb | scale currency to 2 replicas and add a PDB (minAvailable=1) |

## Top findings explained

### 1. frontend-proxy: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Prediction (not yet tested): frontend-proxy - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. frontend-proxy carries gateway share 1.0. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_liveness_probe.yaml
```

_Text source: Template_

### 2. frontend-proxy: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Prediction (not yet tested): frontend-proxy - no readiness probe. Evidence: has_readiness=False - a restarted pod may receive traffic before it's ready. frontend-proxy carries gateway share 1.0. Severity: High.

**Why the fix helps:** A readiness probe keeps traffic away from a pod until it can serve, so a restart does not turn into user-facing errors.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_readiness_probe.yaml
```

_Text source: Template_

### 3. frontend-proxy: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=0

**Why it matters:** Prediction (not yet tested): frontend-proxy - single replica no pdb. Evidence: replicas=1, has_pdb=False, stateful=False, fan_in=0. frontend-proxy carries gateway share 1.0. Severity: High.

**Why the fix helps:** A second replica keeps serving while one pod is killed or rescheduled, and the PodDisruptionBudget stops voluntary evictions (node drains, upgrades) from taking both down.

**Recommended fix:** scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-proxy-single_replica_no_pdb.pdb.yaml
```

_Text source: Template_

### 4. frontend: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Prediction (not yet tested): frontend - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. frontend is called by frontend-proxy; has 1 dependent service(s) in its blast radius; carries gateway share 0.875. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_liveness_probe.yaml
```

_Text source: Template_

### 5. frontend: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Prediction (not yet tested): frontend - no readiness probe. Evidence: has_readiness=False - a restarted pod may receive traffic before it's ready. frontend is called by frontend-proxy; has 1 dependent service(s) in its blast radius; carries gateway share 0.875. Severity: High.

**Why the fix helps:** A readiness probe keeps traffic away from a pod until it can serve, so a restart does not turn into user-facing errors.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_readiness_probe.yaml
```

_Text source: Template_

### 6. frontend: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** Prediction (not yet tested): frontend - single replica no pdb. Evidence: replicas=1, has_pdb=False, stateful=False, fan_in=1. frontend is called by frontend-proxy; has 1 dependent service(s) in its blast radius; carries gateway share 0.875. Severity: High.

**Why the fix helps:** A second replica keeps serving while one pod is killed or rescheduled, and the PodDisruptionBudget stops voluntary evictions (node drains, upgrades) from taking both down.

**Recommended fix:** scale frontend to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-single_replica_no_pdb.pdb.yaml
```

_Text source: Template_

### 7. payment: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Prediction (not yet tested): payment - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. payment is called by checkout; has 3 dependent service(s) in its blast radius; carries gateway share 0.0625; sits on the checkout path. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_liveness_probe.yaml
```

_Text source: Template_

### 8. payment: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Prediction (not yet tested): payment - no readiness probe. Evidence: has_readiness=False - a restarted pod may receive traffic before it's ready. payment is called by checkout; has 3 dependent service(s) in its blast radius; carries gateway share 0.0625; sits on the checkout path. Severity: High.

**Why the fix helps:** A readiness probe keeps traffic away from a pod until it can serve, so a restart does not turn into user-facing errors.

**Recommended fix:** add a readinessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_readiness_probe.yaml
```

_Text source: Template_

### 9. payment: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** Prediction (not yet tested): payment - single replica no pdb. Evidence: replicas=1, has_pdb=False, stateful=False, fan_in=1. payment is called by checkout; has 3 dependent service(s) in its blast radius; carries gateway share 0.0625; sits on the checkout path. Severity: High.

**Why the fix helps:** A second replica keeps serving while one pod is killed or rescheduled, and the PodDisruptionBudget stops voluntary evictions (node drains, upgrades) from taking both down.

**Recommended fix:** scale payment to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-payment-single_replica_no_pdb.pdb.yaml
```

_Text source: Template_

### 10. checkout: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Prediction (not yet tested): checkout - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. checkout is called by frontend; has 2 dependent service(s) in its blast radius; carries gateway share 0.25; sits on the checkout path. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to checkout

```
kubectl -n otel-demo patch deployment checkout --patch-file reports/fixes/R-checkout-no_liveness_probe.yaml
```

_Text source: Template_

## Predicted vs confirmed

| Confirmed | Refuted | Inconclusive | Predicted |
|---|---|---|---|
| 0 | 0 | 0 | 60 |

## Experiments

| Id | Target | Fault | Outcome | S |
|---|---|---|---|---|
| exp-001 | frontend-proxy | pod_kill | not_run | - |
| exp-002 | product-catalog | cpu_stress | not_run | - |
| exp-003 | astronomy-db | network_delay | not_run | - |
| exp-004 | currency | pod_kill | not_run | - |
| exp-005 | cart | cpu_stress | not_run | - |
| exp-006 | shipping | network_delay | not_run | - |
| exp-007 | frontend | pod_kill | not_run | - |
| exp-008 | payment | cpu_stress | not_run | - |

## How the score works

- Per affected journey: S = 100 x (A x 0.45 + L x 0.2 + R x 0.2 + C x 0.15) (Decisions.md D-008).
- Overall score = sum(journey weight x S) / sum(journey weight). Journey weights: checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1.
- Severity of a tested finding = (100 - S) x service criticality; untested findings are predictions ranked by structural risk.
- The AI only explains. Scores and severities are computed by code; every number in AI-written text is checked against the facts and dropped if invented.
