"""Chaos agent (fault planning, injection, safety stop). Ported from P1's chaos_agent (branch chaos-agent, c07af61).

Modules (P1's original file name in brackets):
  k8s_monitor.py  [monitor.py]         kubectl helpers, kubectl-proxy probe, Prometheus signals, pod health, P1 baseline
  executor.py     [chaos_executor.py]  ExperimentPlan validation + Chaos Mesh manifest
  guardian.py     [guardian.py]        safety loop, kill switch, recovery check, --abort
  controller.py   [controller.py]      one run engine, called by src.run (D-033): inject -> guard -> clean up -> measure
  planner.py      [chaos_planner.py]   RETIRED (D-033): Groq LLM chose targets itself; plans come from P3 (src.plan)
  app_mapper.py   [app_mapper.py]      service map from Jaeger + kubectl

Run everything from the repo folder as modules, e.g.  python -m src.chaos.guardian --abort
Committed inputs live in config/, generated outputs in data/chaos/ (data/ is never committed).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data" / "chaos"

SETTINGS_PATH = CONFIG_DIR / "chaos_settings.json"
EXPERIMENT_PATH = CONFIG_DIR / "experiment.json"
SNAPSHOT_PATH = CONFIG_DIR / "service_snapshot.json"
OVERRIDES_PATH = CONFIG_DIR / "app_map_overrides.json"
ENV_PATH = ROOT / ".env"
