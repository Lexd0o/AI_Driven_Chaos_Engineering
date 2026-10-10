# agents/ - third-party agent code

- `chaos_agent_expanded/`: only its watchdog (+ core helpers) is used, by `src/chaos/controller.py`.
- `recovery_agent/`: standalone LangGraph + Groq analysis agent. **Not part of the run path or the scores**
  (D-007: no second agent; scores are P5's, D-008). Kept for reference only.

Treat downloaded agents as untrusted until reviewed: read the code before running it, and run it from the
repo root with paths passed as arguments (`python -I ...`), never from inside its own folder.

The one way to run an experiment is `python -m src.run` (see its docstring).
