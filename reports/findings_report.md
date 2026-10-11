# ChaosPilot findings report

Generated 2026-10-11 04:50 UTC by `python -m src.report` from the run files and stored samples. Every number is computed by code; the LLM did not touch the cluster or the scores.

## Summary

**Overall resilience score: 86.5 / 100** (criticality-weighted mean of the scored experiments).
**Coverage:** 6 of 8 approved experiments scored, 1 inconclusive, 1 not run.

| Exp | Fault | Target | Runs scored | Outcome(s) | S | Severity | Prediction |
|---|---|---|---|---|---|---|---|
| exp-001 | pod_kill | frontend-proxy | 0/0 | not run | n/a | not run | inconclusive |
| exp-002 | cpu_stress | product-catalog | 1/1 | survived | 100.0 | Low | not confirmed (no impact seen) |
| exp-003 | network_delay | astronomy-db | 2/2 | degraded_recovered, degraded_recovered | 82.0 | Medium | confirmed |
| exp-004 | pod_kill | cart | 4/4 | degraded_recovered, degraded_recovered, degraded_recovered, degraded_recovered | 32.5 | High | confirmed |
| exp-005 | cpu_stress | currency | 1/1 | survived | 100.0 | Low | not confirmed (no impact seen) |
| exp-006 | network_delay | shipping | 2/2 | survived, survived | 100.0 | Low | not confirmed (no impact seen) |
| exp-007 | pod_kill | frontend | 0/1 | degraded_recovered | n/a | inconclusive | inconclusive |
| exp-008 | cpu_stress | payment | 1/1 | survived | 100.0 | Low | not confirmed (no impact seen) |

## How the score works

`S = 100 x (0.45 A + 0.20 L + 0.20 R + 0.15 C)` per run, then averaged over repeats, then weighted by service criticality (`config/criticality.yaml`).

- **A** successful requests / offered requests during the fault window; **L** requests that were ok and within the latency limit. Both are weighted across the affected journeys (checkout 0.4, cart 0.3, browse 0.2).
- **R** 1 if the shop survived; otherwise 1 minus impact recovery time over the recovery budget; 0 if it did not recover.
- **C** share of the journeys *not* targeted that stayed within SLO. When every probed journey is a target there is nothing to contain: C is shown as n/a and the other three weights are scaled to add up to 1.
- A journey with fewer samples in the fault window than its minimum is left out. If none remain, the run is **inconclusive** and gets no score.
- Severity = (100 - S) x service criticality: Critical >= 30, High >= 15, Medium >= 5, otherwise Low.

## Findings: impact seen

### cart - pod_kill - High (S 32.5)

**Predicted:** Killing the sole cart pod will cause add‑to‑cart and checkout steps to return errors or time out, and cart functionality will resume once the pod restarts.

**Prediction status:** confirmed. Expected journeys: cart, checkout.

**Measured:**
- r1 `run-e3b91f7b1ac9`: **degraded_recovered** - S 32.7 (A 0.00, L 0.00, R 0.88, C 1.00) - impact recovery 14 s - journeys scored: cart, checkout - numbers recomputed from samples
  - Evidence for r1 (collector quality: ok): 10 trace(s): `951263ef5ea5248a15a8f254080aa1b9`, `c7fc5852dcff3401d3ef1235b06e3875`, `cbcc154bc29dbaece8cdeb8ac7ad5412` (+7 more in the run file); 3 Prometheus queries recorded.
- r2 `run-ae5c70eadf7f`: **degraded_recovered** - S 32.7 (A 0.00, L 0.00, R 0.88, C 1.00) - impact recovery 14 s - journeys scored: cart, checkout - numbers recomputed from samples
  - Evidence for r2 (collector quality: ok): 20 trace(s): `345fe2ee500c9cb727b708fb9f52c87a`, `f97118a078f85eb8050d207410ca84ec`, `03de2fb727050d684a00f29792e9c7c4` (+17 more in the run file); 3 Prometheus queries recorded.
- r3 `run-4287f9064ac8`: **degraded_recovered** - S 32.3 (A 0.00, L 0.00, R 0.87, C 1.00) - impact recovery 16 s - journeys scored: cart, checkout - numbers recomputed from samples
  - Evidence for r3 (collector quality: ok): 20 trace(s): `d3d6737d12b7c7b3e8d64bdd70dd64f8`, `8e5c86332bdc22e7e90e92f799913c9e`, `9ca6bb268b6ede0d83da9a57e242107f` (+17 more in the run file); 3 Prometheus queries recorded.
- r4 `run-f7e5a874dd74`: **degraded_recovered** - S 32.3 (A 0.00, L 0.00, R 0.87, C 1.00) - impact recovery 16 s - journeys scored: cart, checkout - numbers recomputed from samples
  - Evidence for r4 (collector quality: ok): 20 trace(s): `eba1e9b9095dbc6dd3dfd99906a708ae`, `9d8a0f66d82808d9c037707c478f2e05`, `b5636941015bf0fdb6d9cd3baf480dfe` (+17 more in the run file); 3 Prometheus queries recorded.

**Recommendation:**
- Suggested fix (`R-cart-single_replica_no_pdb`, predicted severity high): scale cart to 2 replicas and add a PDB (minAvailable=1)
  - `kubectl -n otel-demo patch deployment cart --patch-file reports/fixes/R-cart-single_replica_no_pdb.yaml`
  - `kubectl apply -f reports/fixes/R-cart-single_replica_no_pdb.pdb.yaml`

### astronomy-db - network_delay - Medium (S 82.0)

**Predicted:** When a 200 ms artificial delay is added to astronomy-db, calls from product‑catalog (affecting browse and checkout) will incur at least 200 ms extra latency, observable as slower page loads, and latency will drop back after the delay ends.

**Prediction status:** confirmed. Expected journeys: browse, checkout.

**Measured:**
- r1 `run-e4ffabff3d22`: **degraded_recovered** - S 80.6 (A 1.00, L 0.53, R 0.50, C 1.00) - impact recovery 60 s - journeys scored: browse, checkout - numbers recomputed from samples
  - Evidence for r1 (collector quality: ok): 11 trace(s): `1242a5b728875d35d0a327cca8de94ce`, `f93c60fc50c4c09362da8d8791fc5328`, `27f3590eff6d7a0d14ebd1666f1835a3` (+8 more in the run file); 3 Prometheus queries recorded.
- r2 `run-0e3e86983032`: **degraded_recovered** - S 83.4 (A 1.00, L 0.66, R 0.51, C 1.00) - impact recovery 58 s - journeys scored: browse, checkout - numbers recomputed from samples
  - Evidence for r2 (collector quality: ok): 11 trace(s): `c1ecd23db0daab9d07c9b97970b13906`, `03aa7d748a12d42d45f5bbb77db35c69`, `35bffa585e76c9d7a27ec6ae43bb0fac` (+8 more in the run file); 3 Prometheus queries recorded.

**Recommendation:**
- Suggested fix (`R-astronomy-db-no_hpa_shared_dependency`, predicted severity medium): absorb load spikes on astronomy-db
  - Manual: astronomy-db is a data store; horizontal autoscaling doesn't apply without replication.

## Tolerated faults: prediction not confirmed

The static analysis predicted a weakness here, and this fault did not expose it. That is a result of this one fault and these runs, not proof the service is safe.

### product-catalog - cpu_stress (S 100.0)

**Predicted:** During the 60‑second CPU stress on product‑catalog, checkout and browse requests will experience errors or added latency from product‑catalog, and normal performance will return after the stress ends.

- r1 `run-80ed8d77deab`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C n/a) - journeys scored: browse, cart, checkout - C not applicable, weights rescaled - unrelated blip at 23:59:53 UTC after the fault ended, not scored - numbers recomputed from samples

- Suggested fix (`R-product-catalog-no_liveness_probe`, predicted severity medium): add a livenessProbe (gRPC health check on port 8080) to product-catalog
  - `kubectl -n otel-demo patch deployment product-catalog --patch-file reports/fixes/R-product-catalog-no_liveness_probe.yaml`

### currency - cpu_stress (S 100.0)

**Predicted:** CPU‑stress on currency will make price‑conversion calls during checkout and browsing slow or fail, and normal price‑lookup latency will recover after the stress period.

- r1 `run-3b8467c8acf1`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C 1.00) - journeys scored: browse, checkout - numbers recomputed from samples

- Suggested fix (`R-currency-no_liveness_probe`, predicted severity medium): add a livenessProbe (gRPC health check on port 8080) to currency
  - `kubectl -n otel-demo patch deployment currency --patch-file reports/fixes/R-currency-no_liveness_probe.yaml`

### shipping - network_delay (S 100.0)

**Predicted:** Injecting a 200 ms network delay into shipping will add latency to shipping‑quote retrieval during checkout, causing slower checkout completion, and latency will return to baseline after the delay ends.

- r1 `run-7f0ff7a036e6`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C 1.00) - journeys scored: cart, checkout - unrelated blip at 23:04:43 UTC after the fault ended, not scored - numbers recomputed from samples
- r2 `run-d16078083a11`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C 1.00) - journeys scored: cart, checkout - numbers recomputed from samples

- Suggested fix (`R-shipping-no_hpa_shared_dependency`, predicted severity medium): autoscale shipping 2-4 replicas at 70% CPU (needs metrics-server)
  - `kubectl apply -f reports/fixes/R-shipping-no_hpa_shared_dependency.hpa.yaml`

### payment - cpu_stress (S 100.0)

**Predicted:** CPU stress on payment will cause payment processing during checkout to error or be delayed, and successful payment responses will return once the stress stops.

- r1 `run-d723ad986dc9`: **survived** - S 100.0 (A 1.00, L 1.00, R 1.00, C 1.00) - journeys scored: checkout - numbers recomputed from samples

- Suggested fix (`R-payment-no_liveness_probe`, predicted severity high): add a livenessProbe (gRPC health check on port 8080) to payment
  - `kubectl -n otel-demo patch deployment payment --patch-file reports/fixes/R-payment-no_liveness_probe.yaml`

## Not scored

### frontend-proxy - pod_kill (exp-001): not run

- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why).

### frontend - pod_kill (exp-007): inconclusive

- r1 `run-a206dc31d8b0`: **degraded_recovered** - not scored: too few samples in the fault window to score (browse n=2, cart n=2, checkout n=2) - observed impact recovery 10 s (a measured time, not a score) - stopped early by the guardian (probe_failure) - numbers recomputed from samples


## Ranking benchmark: ranked order vs random order

Scored experiments in the planner's order: exp-002, exp-003, exp-004, exp-005, exp-006, exp-008.

- The first confirmed finding came at position 2 in the planner's order, versus 2.3 on average over all 720 orderings of the same 6 scored experiments (2 of them hits). A random order would do this well or better 60% of the time.
- The first High or Critical finding came at position 3 in the planner's order, versus 3.5 on average over all 720 orderings of the same 6 scored experiments (1 of them hits). A random order would do this well or better 50% of the time.

_Small sample: experiments scored so far, in the planner's order. This checks the ranking, not the LLM text._

## Notes and limits

- Numbers come from the runs listed above on one cluster. Repeats are shown where they exist; a single run is one observation.
- A pod kill is instant, so its fault window holds few samples; the recovery time is the more reliable number for those runs.
- For timed faults (network delay, CPU stress) the impact recovery time runs from the first breach to the start of a healthy window, so it includes the time the fault itself was active. R is therefore capped by the fault length for those runs; the recovery after the fault ended is in the run files (`post_fault_recovery_s`).
- A short unrelated failure after a fault ended is listed on the run as a blip and is not scored; the shop shows a low background error rate even with no fault running (source not yet found).
- Runs that were stopped on purpose to test the kill switch (forced breach) are not part of this report.
- Fix files in `reports/fixes/` are suggestions; nothing was applied to the cluster.
- Trace IDs open at `/jaeger/ui/trace/<id>` on the Jaeger the run used. Jaeger keeps traces in memory, so an ID stops resolving after Jaeger restarts; the IDs and queries stay in `data/runs/*.json`.
