# ChaosPilot findings report

Snapshot: scores generated 2026-10-11T01:29:49.395926+00:00, graph generated 2026-10-11T00:46:29.523548+00:00

## Resilience score: 73.0 / 100

Coverage: 3 of 8 planned experiments scored

| Category | Score | Experiments |
|---|---|---|
| Redundancy | 100.0 | 1 |
| Dependency | 100.0 | 1 |
| Capacity | 19.1 | 1 |

## Executive summary

_AI-written - numbers come from code_

The OpenTelemetry Demo shows a solid redundancy and dependency posture but a weak capacity profile, reflected in a 73 resilience score and only 3 of 8 planned experiments scored. The most critical issue is the missing liveness probe on product-catalog, which caused real outages in the ads-recs and browse journeys. Addressing this probe first will restore automatic recovery for a key service and immediately improve overall availability.

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

**Why it matters:** Without a liveness probe, a hung product-catalog process is not restarted by Kubernetes. During the experiment the service failed, causing the ads-recs and browse journeys to drop to 25 % availability and 25 % of requests to exceed the latency SLO. The evidence shows the pod was aborted because the probe threshold was breached, and the failure was not recovered.

**Why the fix helps:** Adding a livenessProbe (gRPC health check on port 8080) will cause Kubernetes to restart a hung product-catalog pod, restoring service availability and preventing downstream journey failures.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to product-catalog

```
kubectl -n otel-demo patch deployment product-catalog --patch-file reports/fixes/R-product-catalog-no_liveness_probe.yaml
```

_Text source: AI-written_

### 2. frontend-proxy: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The frontend-proxy lacks a liveness probe, so if it hangs it will not be restarted. This could cause any journey that routes through the proxy-such as browse, ads-recs, or checkout-to receive traffic from an unhealthy pod, leading to errors or degraded performance. The evidence indicates the probe is missing and a hung process would not be auto-restarted.

**Why the fix helps:** Adding a livenessProbe (tcpSocket on the service port) will enable Kubernetes to detect and restart a hung frontend-proxy pod, ensuring continuous routing for all dependent journeys.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_liveness_probe.yaml
```

_Text source: AI-written_

### 3. frontend-proxy: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, a restarting frontend-proxy pod may receive traffic before it is fully ready, potentially returning errors to any journey that uses the proxy. The evidence shows readiness is absent, meaning traffic could be sent to an unprepared pod.

**Why the fix helps:** Adding a readinessProbe (tcpSocket on the service port) ensures the proxy only receives traffic when it is fully ready, reducing the chance of user-facing errors during restarts.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_readiness_probe.yaml
```

_Text source: AI-written_

### 4. frontend-proxy: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=0

**Why it matters:** The frontend-proxy runs a single replica without a PodDisruptionBudget. During a voluntary eviction or node failure, the only pod could be removed, causing a complete outage for all journeys that depend on the proxy. The evidence notes the replica count is one and no PDB is configured.

**Why the fix helps:** Scaling to two replicas and adding a PDB (minAvailable = 1) guarantees at least one healthy pod remains during disruptions, maintaining continuous service for all dependent journeys.

**Recommended fix:** scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-proxy-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 5. frontend: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The frontend service has no liveness probe, so a hung frontend process would not be restarted. This could affect any journey that calls the frontend, such as browse or ads-recs, by returning errors or high latency. The evidence shows the probe is missing and a hung process would not be auto-restarted.

**Why the fix helps:** Adding a livenessProbe (tcpSocket on the service port) will allow Kubernetes to restart a hung frontend pod, preserving availability for all journeys that rely on it.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_liveness_probe.yaml
```

_Text source: AI-written_

### 6. frontend: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, a restarting frontend pod may receive traffic before it is ready, potentially causing errors for journeys that call the frontend. The evidence indicates readiness is absent, meaning traffic could be sent to an unprepared pod.

**Why the fix helps:** Adding a readinessProbe (tcpSocket on the service port) ensures the frontend only receives traffic when it is fully ready, reducing user-facing errors during restarts.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_readiness_probe.yaml
```

_Text source: AI-written_

### 7. frontend: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** The frontend runs a single replica without a PodDisruptionBudget. A voluntary eviction or node failure could remove the only pod, causing a full outage for all journeys that call the frontend. The evidence notes the replica count is one and no PDB is configured.

**Why the fix helps:** Scaling to two replicas and adding a PDB (minAvailable = 1) guarantees at least one healthy pod remains during disruptions, maintaining continuous service for all dependent journeys.

**Recommended fix:** scale frontend to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 8. payment: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The payment service lacks a liveness probe, so a hung payment process would not be restarted. This could break the checkout journey, which depends on payment, by causing errors or high latency. The evidence shows the probe is missing and a hung process would not be auto-restarted.

**Why the fix helps:** Adding a livenessProbe (gRPC health check on port 8080) will enable Kubernetes to restart a hung payment pod, ensuring checkout can complete successfully.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_liveness_probe.yaml
```

_Text source: AI-written_

### 9. payment: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, a restarting payment pod may receive traffic before it is ready, potentially causing checkout errors. The evidence indicates readiness is absent, meaning traffic could be sent to an unprepared pod.

**Why the fix helps:** Adding a readinessProbe (gRPC health check on port 8080) ensures the payment service only receives traffic when it is fully ready, reducing checkout failures during restarts.

**Recommended fix:** add a readinessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_readiness_probe.yaml
```

_Text source: AI-written_

### 10. payment: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** The payment service runs a single replica without a PodDisruptionBudget. A voluntary eviction or node failure could remove the only pod, causing a checkout outage. The evidence notes the replica count is one and no PDB is configured.

**Why the fix helps:** Scaling to two replicas and adding a PDB (minAvailable = 1) guarantees at least one healthy payment pod remains during disruptions, preserving checkout availability.

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

## Ranking benchmark

Our ranking found the first confirmed weakness at position 1 of 3. Random orderings average position 1.96 (analytic expectation 2.00); 36% of 1000 random orderings (seed 42) do as well or better.

_Caveat: Small sample: 3 experiments, 1 hit(s); not statistically significant._

## How the score works

- Per affected journey: S = 100 x (A x 0.45 + L x 0.2 + R x 0.2 + C x 0.15) (Decisions.md D-008).
- Overall score = sum(journey weight x S) / sum(journey weight). Journey weights: checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1.
- Severity of a tested finding = (100 - S) x service criticality; untested findings are predictions ranked by structural risk.
- The AI only explains. Scores and severities are computed by code; every number in AI-written text is checked against the facts and dropped if invented.
