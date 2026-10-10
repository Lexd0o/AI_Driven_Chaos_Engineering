# chaos_agent_expanded (vendored)

Inject + guard + stop layer for ChaosPilot. Driven only by `src/run.py` (Rules.md rule 2);
not run on its own.

Origin: the third-party "Expanded OpenTelemetry Demo chaos agent" (Groq + Chaos Mesh), copied
11 Oct 2026 and reviewed before use. It only calls `kubectl` with an explicit context and
namespace, and deletes only Chaos Mesh resources that carry its own `reslab-run-id` label.

## Kept from upstream
- `core.py`: pydantic `ExperimentPlan`, manifest builder, owned-resource cleanup.
- `guardian.py`: HTTP probe and a sustained-breach guardrail.
- `watchdog.py`: a detached process that deletes our CR at the deadline even if the
  controller dies (safety layer L2, alongside Chaos Mesh's own `duration`).
- `lifecycle.py`: upstream `controller.run()`. It checks for a healthy baseline, confirms
  `can-i delete` and runs a server dry-run, then injects, waits for `AllInjected` and monitors.
  It removes the fault in `finally`.

## Changed for ChaosPilot
- Added `cpu_stress` → `StressChaos` (our `config/fault_catalog.yaml` uses it).
- Duration cap comes from `config/policy.yaml` (`max_duration_s`), not a hard-coded 45 s;
  the fault deadline is `duration + grace_seconds`.
- Probes several journeys per sample (`probe_journeys`).
- Added a post-fault recovery watch (`t_clear`, `t_healthy`, Architecture.md §7). Upstream
  left this to a separate recovery agent.
- Settings come from `config/executor.yaml` + `config/policy.yaml` via `src/run.py`.
  Paths go under `data/`.

## Dropped
- The Groq planner (`chaos_planner.py`, its `policy.py` prompt). Our `src.plan` +
  `src.policy` produce the plan, and the LLM never acts (D-005).
- `controller.py` CLI, `chaos_executor.py`, `inspect_cluster.py` (read-only flagd discovery,
  not needed until flag faults are added), and `settings.json` / `experiment.json`.
