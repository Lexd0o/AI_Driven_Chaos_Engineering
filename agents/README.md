# agents/ — third-party agents under test

This folder lives in the `agent-testing` worktree (branch `agent-testing`), an isolated copy
of the ChaosPilot repo. Nothing here affects the main checkout on `p3-live-pipeline`.

## Adding an agent
- One agent per folder: `agents/<agent-name>/`, starting empty, holding only that agent's files.
- Treat downloaded agents as untrusted until reviewed: read the code before running it, and
  run it from the repo root with paths passed as arguments (`python -I ...`), never from inside
  its own folder, so a planted module can't shadow a real one.
- Check for anything that touches the cluster. Rules.md applies: no kubectl/chaos actions
  outside `run.py`, never target `kube-system`, `chaos-mesh`, or monitoring.

## Shared with the main checkout (be careful)
- **Same live cluster** (`docker-desktop`, namespace `otel-demo`) and the same port-forward on
  http://localhost:8080. The node has ~1% memory headroom: anything new scheduled on it can
  evict app pods (flagd was evicted once already).
- **Same Jaeger.** It's in-memory with a 600Mi limit; don't run `src.discover` here and in the
  main checkout at the same time, and avoid large trace queries — an OOM wipes all traces.

## Pipeline in this copy
    python -m src.discover && python -m src.audit && python -m src.rank \
      && python -m src.plan && python -m src.policy && python -m src.fixes
Outputs go to this worktree's own `data/` and `reports/`.

Then one experiment at a time (injector = `agents/chaos_agent_expanded/`):
    python -m src.run --list                    # which approved experiments are runnable
    python -m src.run --exp exp-004 --preview   # server dry-run, nothing created
    python -m src.run --exp exp-004 --execute   # real fault -> data/runs/exp-004-r1.json
    python -m src.run --stop-all                # emergency: delete all chaos CRs + data/STOP
Tests: `python -m unittest tests.test_run -v`
