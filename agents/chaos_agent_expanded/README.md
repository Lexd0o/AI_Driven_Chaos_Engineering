# chaos_agent_expanded (vendored, partly)

Origin: the third-party "Expanded OpenTelemetry Demo chaos agent" (Groq + Chaos Mesh), copied 11 Oct 2026
and reviewed by P4 before use.

## Used by the hybrid run path (integration, 11 Oct)
- `watchdog.py`: a detached process that deletes our chaos object at a deadline even if the controller dies
  (safety layer L2, next to Chaos Mesh's own `duration`). Armed by `src/chaos/controller.py` before every
  real injection, disarmed after cleanup is confirmed, left armed if cleanup failed.
- `core.py`: what the watchdog needs (owned-only cleanup by the `reslab-run-id` label, kubectl helpers).

## Retired (one run path, one guardian)
- `lifecycle.py` (inject + monitor) and `guardian.py` (HTTP guardrail): replaced by P1's controller and
  guardian, which were tested live (G3). Its GET probes and recovery watch are replaced by P2's sampler
  and run metrics, which also cover checkout.
- Earlier: the Groq planner, `controller.py` CLI, `chaos_executor.py`, `inspect_cluster.py`.

Its ideas that moved into our code: `cpu_stress` -> StressChaos (`src/chaos/executor.py`),
`can-i delete` before injecting and the STOP file (`src/chaos/controller.py`).
