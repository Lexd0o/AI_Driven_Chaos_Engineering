# ChaosPilot findings report

Snapshot: scores generated 2026-10-11T04:53:30.456091+00:00, graph generated 2026-10-11T00:46:29.523548+00:00

## Resilience score: 73.2 / 100

Coverage: 4 of 8 planned experiments scored

| Category | Score | Experiments |
|---|---|---|
| Redundancy | 100.0 | 1 |
| Dependency | 86.1 | 2 |
| Capacity | 20.6 | 1 |

## Executive summary

_AI-written - numbers come from code_

The OpenTelemetry Demo achieved a resilience score of 73.2 after testing 4 of 8 planned experiments. The most critical weakness is the product-catalog service lacking a liveness probe, which caused failures in the ads-recs, browse and checkout journeys. Addressing this probe and improving load-spike handling for astronomy-db should be the first priority to raise overall resilience.

## Fix verification

Overall score: 73.2 before fixes, 70.8 after fixes (labelled re-runs replace the unlabelled score of the same experiment).

| Experiment | Label | Before S | Before outcome | After S | After outcome | Delta S |
|---|---|---|---|---|---|---|
| exp-002 | fix-applied | 20.6 | not_recovered | 10.9 | not_recovered | -9.7 |

## Weaknesses ranked by severity

| Rank | Severity | Status | Service | Weakness | Fix |
|---|---|---|---|---|---|
| 1 | High | confirmed | product-catalog | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to product-catalog |
| 2 | Medium | confirmed | astronomy-db | no hpa shared dependency | absorb load spikes on astronomy-db |
| 3 | High | predicted | frontend-proxy | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend-proxy |
| 4 | High | predicted | frontend-proxy | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend-proxy |
| 5 | High | predicted | frontend-proxy | single replica no pdb | scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1) |
| 6 | High | predicted | frontend | no liveness probe | add a livenessProbe (tcpSocket on port 'service') to frontend |
| 7 | High | predicted | frontend | no readiness probe | add a readinessProbe (tcpSocket on port 'service') to frontend |
| 8 | High | predicted | frontend | single replica no pdb | scale frontend to 2 replicas and add a PDB (minAvailable=1) |
| 9 | High | predicted | payment | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to payment |
| 10 | High | predicted | payment | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to payment |
| 11 | High | predicted | payment | single replica no pdb | scale payment to 2 replicas and add a PDB (minAvailable=1) |
| 12 | High | predicted | checkout | no liveness probe | add a livenessProbe (gRPC health check on port 8080) to checkout |
| 13 | High | predicted | checkout | no readiness probe | add a readinessProbe (gRPC health check on port 8080) to checkout |
| 14 | High | predicted | checkout | single replica no pdb | scale checkout to 2 replicas and add a PDB (minAvailable=1) |
| 15 | High | predicted | product-catalog | single replica no pdb | scale product-catalog to 2 replicas and add a PDB (minAvailable=1) |

## Top findings explained

### 1. product-catalog: no liveness probe

**Confirmed by exp-002** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The product-catalog service was unable to recover from a CPU-stress fault, causing the ads-recs, browse and checkout journeys to fail. The failure was confirmed by a peak error rate of 1.0 and an abort due to a probe threshold breach, showing that a hung process was not restarted by Kubernetes.

**Why the fix helps:** Adding a liveness probe that performs a gRPC health check on port 8080 will allow Kubernetes to detect and restart a hung product-catalog pod, preventing prolonged outages for the affected journeys.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to product-catalog

```
kubectl -n otel-demo patch deployment product-catalog --patch-file reports/fixes/R-product-catalog-no_liveness_probe.yaml
```

_Text source: AI-written_

### 2. astronomy-db: no hpa shared dependency

**Confirmed by exp-003** - severity **Medium**

**Evidence:** has_hpa=False, 2 callers - can't absorb a traffic or latency spike

**Why it matters:** The astronomy-db service, which has no horizontal pod autoscaler, suffered degraded performance under a network-delay fault. All measured journeys-ads-recs, browse and checkout-experienced availability below the 0.95 threshold, indicating that the service could not absorb traffic spikes.

**Why the fix helps:** Implementing a strategy to absorb load spikes, such as adding replication or a custom scaling mechanism, would enable astronomy-db to handle sudden traffic increases and prevent downstream degradation.

**Recommended fix:** absorb load spikes on astronomy-db

Manual step: astronomy-db is a data store; horizontal autoscaling doesn't apply without replication.

_Text source: AI-written_

### 3. frontend-proxy: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The frontend-proxy service lacks a liveness probe, meaning a hung process would not be automatically restarted. This could affect any journey that routes traffic through the proxy, potentially causing widespread failures.

**Why the fix helps:** Adding a liveness probe that checks a TCP socket on the service port will enable Kubernetes to restart hung proxy pods, reducing the risk of extended outages.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_liveness_probe.yaml
```

_Text source: AI-written_

### 4. frontend-proxy: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, the frontend-proxy may receive traffic before a pod is fully ready, leading to errors for users. This risk could impact all journeys that depend on the proxy.

**Why the fix helps:** Adding a readiness probe that checks a TCP socket on the service port ensures traffic is only sent to fully ready pods, improving reliability for all dependent journeys.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend-proxy

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-no_readiness_probe.yaml
```

_Text source: AI-written_

### 5. frontend-proxy: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=0

**Why it matters:** The frontend-proxy runs a single replica without a pod disruption budget, so a pod failure or eviction could bring down the entire proxy, affecting all traffic that passes through it.

**Why the fix helps:** Scaling the proxy to two replicas and adding a pod disruption budget protects against pod failures and evictions, providing redundancy for all dependent journeys.

**Recommended fix:** scale frontend-proxy to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend-proxy --patch-file reports/fixes/R-frontend-proxy-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-proxy-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 6. frontend: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The frontend service has no liveness probe, so a hung frontend pod would not be restarted, potentially disrupting all downstream services and user journeys.

**Why the fix helps:** Adding a liveness probe that checks a TCP socket on the service port will allow Kubernetes to restart hung frontend pods, maintaining service availability.

**Recommended fix:** add a livenessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_liveness_probe.yaml
```

_Text source: AI-written_

### 7. frontend: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, the frontend may receive traffic before it is fully ready, causing errors for users across all journeys that rely on it.

**Why the fix helps:** Adding a readiness probe that checks a TCP socket on the service port ensures only ready frontend pods receive traffic, improving reliability.

**Recommended fix:** add a readinessProbe (tcpSocket on port 'service') to frontend

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-no_readiness_probe.yaml
```

_Text source: AI-written_

### 8. frontend: single replica no pdb

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** replicas=1, has_pdb=False, stateful=False, fan_in=1

**Why it matters:** The frontend runs a single replica without a pod disruption budget, so a pod failure or eviction could cause an outage for all journeys that depend on it.

**Why the fix helps:** Scaling the frontend to two replicas and adding a pod disruption budget provides redundancy and protects against pod evictions, reducing outage risk.

**Recommended fix:** scale frontend to 2 replicas and add a PDB (minAvailable=1)

```
kubectl -n otel-demo patch deployment frontend --patch-file reports/fixes/R-frontend-single_replica_no_pdb.yaml
kubectl apply -f reports/fixes/R-frontend-single_replica_no_pdb.pdb.yaml
```

_Text source: AI-written_

### 9. payment: no liveness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_liveness=False - a hung (not crashed) process won't be auto-restarted

**Why it matters:** The payment service lacks a liveness probe, meaning a hung payment pod would not be restarted, potentially breaking the checkout journey.

**Why the fix helps:** Adding a liveness probe that performs a gRPC health check on port 8080 will allow Kubernetes to restart hung payment pods, preventing checkout failures.

**Recommended fix:** add a livenessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_liveness_probe.yaml
```

_Text source: AI-written_

### 10. payment: no readiness probe

**Predicted (static audit, not yet tested)** - severity **High**

**Evidence:** has_readiness=False - a restarted pod may receive traffic before it's ready

**Why it matters:** Without a readiness probe, the payment service may receive traffic before it is ready, which could cause checkout errors for users.

**Why the fix helps:** Adding a readiness probe that performs a gRPC health check on port 8080 ensures traffic is only sent to ready payment pods, improving checkout reliability.

**Recommended fix:** add a readinessProbe (gRPC health check on port 8080) to payment

```
kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_readiness_probe.yaml
```

_Text source: AI-written_

## Predicted vs confirmed

| Confirmed | Refuted | Inconclusive | Predicted |
|---|---|---|---|
| 2 | 2 | 0 | 56 |

## Experiments

| Id | Target | Fault | Outcome | S |
|---|---|---|---|---|
| exp-001 | frontend-proxy | pod_kill | not_run | - |
| exp-002 | product-catalog | cpu_stress | not_recovered | 20.6 |
| exp-003 | astronomy-db | network_delay | degraded_recovered | 72.3 |
| exp-004 | currency | pod_kill | survived | 100.0 |
| exp-005 | cart | cpu_stress | not_run | - |
| exp-006 | shipping | network_delay | survived | 100.0 |
| exp-007 | frontend | pod_kill | not_run | - |
| exp-008 | payment | cpu_stress | not_run | - |

## Ranking benchmark

Our ranking found the first confirmed weakness at position 1 of 4. Random orderings average position 1.66 (analytic expectation 1.67); 51% of 1000 random orderings (seed 42) do as well or better.

_Caveat: Small sample: 4 experiments, 2 hit(s); not statistically significant._

## How the score works

- Per affected journey: S = 100 x (A x 0.45 + L x 0.2 + R x 0.2 + C x 0.15) (Decisions.md D-008).
- Overall score = sum(journey weight x S) / sum(journey weight). Journey weights: checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1.
- Severity of a tested finding = (100 - S) x service criticality; untested findings are predictions ranked by structural risk.
- The AI only explains. Scores and severities are computed by code; every number in AI-written text is checked against the facts and dropped if invented.
