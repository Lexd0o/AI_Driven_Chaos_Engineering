# CONTEXT.md — START HERE

> Paste this file (plus `progress.md`) into any new AI model or chat to resume work.
> It is the 1-page "where are we" summary. Details live in the other files.

## 1. What we are building (10 lines)

- **Event:** Bosch hackathon, 2 days, MIT Bengaluru (Oct 2026). Team of 5.
- **Problem:** "AI-Driven Chaos Engineering for Cloud-Native Resilience".
- **One-liner:** An AI system that studies a cloud-native app, decides what to break first and why, breaks it safely (with automatic stop), measures the damage and recovery, and writes a ranked report of weaknesses with concrete fixes and a resilience score.
- **Project name (working):** ChaosPilot
- **Target app:** OpenTelemetry Demo (open-source e-commerce microservices app) on a single-node k3s Kubernetes cluster inside one shared Linux VM.
- **Fault tools:** Chaos Mesh (real infra faults: pod kill, network delay/loss, CPU stress) + the demo's built-in feature flags (app faults: payment failure, cart failure, etc.).
- **AI role:** deterministic risk ranking + LLM writes hypotheses and explanations. **The LLM never touches the cluster directly.** Policy engine validates every plan.
- **Approach:** "Trimmed hybrid" — must-have core (Tier 0) + cheap differentiators (Tier 1) + optional stretch (Tier 2).
- **Pitch line:** "Detect risks statically, prove them with safe chaos, recommend fixes as code, and show our ranking beats random."

## 2. File map

| File | What it holds | When to read |
|---|---|---|
| `CONTEXT.md` | This summary | Always first |
| `progress.md` | Live task board, gate status, blockers, log | Always second |
| `Plan.md` | Full plan: scope tiers, timeline, roles, gates, risks | Planning / re-planning |
| `Requirementsandsetup.md` | Requirements from the slides + VM/cluster setup commands | Setup, compliance checks |
| `Rules.md` | Team rules + rules for AI assistants | Before writing any code |
| `techstack.md` | Tools, libraries, versions, fallbacks | Before adding a dependency |
| `Architecture.md` | Components, data contracts (JSON), safety layers, scoring | Before building a module |
| `Experiments.md` | Fault catalog, hypotheses, results log | Running experiments |
| `Decisions.md` | Decision log (why we chose X) — do not re-debate these | When tempted to change direction |
| `Demo.md` | 3-minute video script + recording checklist | Day 2 afternoon |

## 3. Current phase

- **Phase:** see the top of `progress.md` (single source of truth for status).
- **Open questions:** see "Open questions" in `progress.md`.

## 4. Non-negotiables (short version of Rules.md)

1. The LLM proposes; the policy validates; the executor acts. No LLM shell access.
2. Only one experiment runs at a time (experiment lock).
3. Every fault has a hard duration cap and a working stop command.
4. Never target `kube-system`, `chaos-mesh`, or the monitoring components.
5. Scores are computed by code, not by the LLM. The LLM only explains.
6. Code style: flat, simple Python functions; no class hierarchies.
7. Feature freeze at T−3h before submission. No exceptions.
8. Update `progress.md` after finishing any task.

## 5. How to hand off to a new model

Paste: `CONTEXT.md` + `progress.md` + the one file relevant to your task (e.g. `Architecture.md` for coding). Then state: "I am P<n>, working on <task id>."
