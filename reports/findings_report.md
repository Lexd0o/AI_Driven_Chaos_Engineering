# ChaosPilot findings report

Snapshot: scores generated 2026-10-11T01:05:15.383789+00:00, graph generated 2026-10-11T00:46:29.523548+00:00

## Resilience score: 73.0 / 100

Coverage: 3 of 8 planned experiments scored

| Category | Score | Experiments |
|---|---|---|
| Redundancy | 100.0 | 1 |
| Dependency | 100.0 | 1 |
| Capacity | 19.1 | 1 |

## Executive summary

_AI-written - numbers come from code_

The OpenTelemetry Demo scored 73 out of 100, with only 3 of 8 planned experiments executed. Redundancy and dependency are strong, but capacity is low, indicating that the system can handle failures but may not sustain high load. The most critical issue is the missing liveness probe on product-catalog, which caused real failures in the ads-recs and browse journeys. Addressing this probe first will restore stability for those journeys and reduce the overall risk.

## Weaknesses ranked by severity

| Rank | Severity | Status | Service | Weakness | Fix |
|---|---|---|---|---|---|
| 1 | High | confirmed | product-catalog | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to product-catalog |
| 2 | High | predicted | frontend-proxy | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend-proxy |
| 3 | High | predicted | frontend-proxy | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend-proxy |
| 4 | High | predicted | frontend-proxy | single replica no pdb | scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1) |
| 5 | High | predicted | frontend | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend |
| 6 | High | predicted | frontend | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend |
| 7 | High | predicted | frontend | single replica no pdb | scale frontend to 2 replicas and add a PDB (minAvailable=1) |
| 8 | High | predicted | payment | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to payment |
| 9 | High | predicted | payment | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to payment |
| 10 | High | predicted | payment | single replica no pdb | scale payment to 2 replicas and add a PDB (minAvailable=1) |
| 11 | High | predicted | checkout | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to checkout |
| 12 | High | predicted | checkout | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to checkout |
| 13 | High | predicted | checkout | single replica no pdb | scale checkout to 2 replicas and add a PDB (minAvailable=1) |
| 14 | High | predicted | product-catalog | single replica no pdb | scale product-catalog to 2 replicas and add a PDB (minAvailable=1) |
| 15 | High | predicted | astronomy-db | stateful single replica | make astronomy-db highly available |

## Top findings explained

### 1. product-catalog: no liveness probe

**Confirmed by exp-002** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Confirmed by experiment: product-catalog - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. product-catalog is called by checkout, frontend, recommendation; has 4 dependent service(s) in its blast radius; carries gateway share 0.125; sits on the checkout path. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to product-catalog

```
kubectl -n otel-demo patch deployment product-catalog --patch-file reports/fixes/R-product-catalog-no_liveness_probe.yaml
```

_Text source: Template_

### 2. frontend-proxy: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** Prediction (not yet tested): frontend-proxy - no liveness probe. Evidence: has_liveness=False - a hung (not crashed) process won't be auto-restarted. frontend-proxy carries gateway share 1.0. Severity: High.

**Why the fix helps:** A liveness probe restarts a process that is hung or frozen instead of leaving it to time out every request.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_liveness_probe.yaml
```

_Text source: Template_

### 3. frontend-proxy: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** A missing readiness probe means a restarted frontend-proxy pod might receive traffic before it is fully ready, causing transient failures for journeys that route through it. The evidence indicates has_readiness=False, so traffic could be sent to an unready pod.

**Why the fix helps:** Adding a readiness probe (tcpSocket on the service port) guarantees that traffic is only sent to a pod that is fully ready, reducing the chance of user-facing errors during restarts.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_readiness_probe.yaml
```

_Text source: AI-written_

### 4. frontend-proxy: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=0

**Why it matters:** Running frontend-proxy with a single replica and no Pod Disruption Budget means that any voluntary or involuntary disruption (e.g., node drain) will cause a full outage for all journeys that rely on the gateway. The evidence shows replicas=1 and has_pdb=False.

**Why the fix helps:** Scaling to two replicas and adding a PDB (minAvailable=1) ensures that at least one pod remains available during disruptions, maintaining continuous service for all dependent journeys.

**Recommended fix:** scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-proxy-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 5. frontend: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The frontend service lacks a liveness probe, so if it hangs, downstream services such as ad, cart, checkout, and others will not be restarted automatically, leading to failures in any journey that calls frontend. The evidence shows has_liveness=False.

**Why the fix helps:** Adding a liveness probe (tcpSocket on the service port) will trigger a restart of the frontend pod when it becomes unresponsive, preventing cascading failures to its callers.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_liveness_probe.yaml
```

_Text source: AI-written_

### 6. frontend: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, a restarted frontend pod could receive traffic before it is ready, causing brief outages for journeys that depend on it. The evidence shows has_readiness=False.

**Why the fix helps:** Implementing a readiness probe (tcpSocket on the service port) ensures that traffic is only routed to a fully ready frontend pod, improving reliability during restarts.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_readiness_probe.yaml
```

_Text source: AI-written_

### 7. frontend: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** Frontend runs with a single replica and no PDB, so any disruption will bring down all journeys that call frontend, such as browse and checkout. The evidence indicates replicas=1 and has_pdb=False.

**Why the fix helps:** Scaling to two replicas and adding a PDB (minAvailable=1) guarantees that at least one pod remains available during disruptions, preserving service for all dependent journeys.

**Recommended fix:** scale frontend to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 8. payment: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The payment service has no liveness probe, so a hung payment pod would not be restarted, potentially blocking checkout journeys that rely on it. The evidence shows has_liveness=False.

**Why the fix helps:** Adding a liveness probe (gRPC health check on port 8080) will restart a hung payment pod, preventing checkout failures caused by an unresponsive payment service.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_liveness_probe.yaml
```

_Text source: AI-written_

### 9. payment: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, a restarted payment pod might receive checkout traffic before it is ready, causing checkout failures. The evidence shows has_readiness=False.

**Why the fix helps:** Adding a readiness probe (gRPC health check on port 8080) ensures checkout traffic is only sent to a ready payment pod, reducing the chance of transaction errors during restarts.

**Recommended fix:** add a readinessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_readiness_probe.yaml
```

_Text source: AI-written_

### 10. payment: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** Payment runs with a single replica and no PDB, so any disruption will take down checkout journeys that depend on it. The evidence indicates replicas=1 and has_pdb=False.

**Why the fix helps:** Scaling payment to two replicas and adding a PDB (minAvailable=1) ensures that at least one pod remains available during disruptions, keeping checkout functional.

**Recommended fix:** scale payment to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-payment-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

## Predicted vs confirmed

| Confirmed | Refuted | Inconclusive | Predicted |
|---|---|---|---|
| 1 | 2 | 0 | 57 |

## Experiments

| Id | Target | Fault | Outcome | S |
|---|---|---|---|---|
| exp-001 | frontend-proxy | pod_kill | not_run | - |
| exp-002 | product-catalog | cpu_stress | not_recovered | 19.1 |
| exp-003 | astronomy-db | network_delay | not_run | - |
| exp-004 | currency | pod_kill | survived | 100.0 |
| exp-005 | cart | cpu_stress | not_run | - |
| exp-006 | shipping | network_delay | survived | 100.0 |
| exp-007 | frontend | pod_kill | not_run | - |
| exp-008 | payment | cpu_stress | not_run | - |

## How the score works

- Per affected journey: S = 100 x (A x 0.45 + L x 0.2 + R x 0.2 + C x 0.15) (Decisions.md D-008).
- Overall score = sum(journey weight x S) / sum(journey weight). Journey weights: checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1.
- Severity of a tested finding = (100 - S) x service criticality; untested findings are predictions ranked by structural risk.
- The AI only explains. Scores and severities are computed by code; every number in AI-written text is checked against the facts and dropped if invented.
