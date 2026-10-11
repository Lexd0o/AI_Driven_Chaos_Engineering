# Recovery & Solutions agent

Standalone LangGraph agent. After a fault-injection run it: verifies the fault really happened, analyses the impact,
ranks weaknesses by severity with a resilience score, records recovery time, and recommends fixes.
It shares no code with the injector; it only reads that run's JSON report.

## Setup
```
python -m venv .venv && .venv\Scripts\activate      # or source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                               # add GROQ_API_KEY
```

## Run
```
python recovery_agent.py --report path/to/run-<id>.json
python recovery_agent.py --latest --reports-dir ../chaos_agent_complete/reports
python recovery_agent.py --report examples/sample_run_synthetic.json   # smoke test, synthetic data
python recovery_agent.py --report ... --no-llm                          # deterministic only, labeled fallback
python recovery_agent.py --graph                                        # Mermaid diagram
python -m unittest discover -s tests -v
```
Outputs `<run_id>.recovery.json` and `.recovery.md` (next to the report, or in `--reports-dir`).
Importable: `from recovery_agent import analyze_report`.

## Graph
`load -> windows -> verify -> (UNCONFIRMED: stop) -> recovery -> detect+score -> narrate (LLM) -> validate -> (retry | fallback) -> assemble`

Code computes every number. The LLM (Groq, `langchain-groq`) only explains findings and picks actions from a fixed
catalog. Output is schema-checked and number-checked; 3 failed attempts or any API error use a template, recorded as
`narrative.source = "fallback_template"`. Swap providers by editing `build_llm()`.

## Input contract (run report JSON)
Required: `run_id`, `mode` ("execute"), `plan` {target_service, fault_type: pod_kill|pod_failure|network_delay|packet_loss|packet_duplicate,
duration_seconds, latency_ms, loss_percent, duplicate_percent, pod_count}, `manifest`, `resource.name`, `events` [{stage, observed_at}] containing
`submitting`, `injection_confirmed`, `cleanup`; `fault_injected`, `fault_confirmed_at`, `cleanup_confirmed`,
`cleanup_observed_at`, `stop_reason`, and `samples` [{observed_at, ok, latency_ms, error, stage}] with stages
`baseline`, `during_fault`, `after_cleanup`. Optional: `snapshot.services[{id, replicas}]`,
`post_cleanup_probe_recovery_seconds`. To feed a different injector, map its output to this shape.
The expanded injector does not write the cleanup fields or `after_cleanup` samples; `reslab_pipeline/` probes after
cleanup and adapts its report into this shape (see `../../reslab_pipeline/README.md`).

## Logic
- **Verdict:** CONFIRMED / PARTIAL (injected, no measurable impact) / UNCONFIRMED (stops here).
- **Findings:** error_rate, slo_breach, latency_inflation, slow_recovery, single_replica. Each has a penalty
  (weight x normalized magnitude); severity from penalty points; ranked by penalty.
- **Score:** 100 minus penalties; withheld (null) if baseline/fault windows are too small.
- **Recovery:** pod_kill from fault confirmation; timed faults (network faults, pod_failure) from cleanup. Needs N consecutive healthy samples
  after the last degraded one. Time to first impact and fault-active time are recorded too.
- Tunables: `settings.json` (`probe`, `recovery`).

## Limits
Probe-only evidence (no per-service metrics or traces), one run, heuristic score, recommendations are proposals
that were not applied or verified. Tests use a stub LLM and synthetic reports; the live Groq call is not covered.
