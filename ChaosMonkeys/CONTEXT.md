# CONTEXT.md — START HERE

> Paste this file (plus `progress.md`) into any new AI model or chat to resume work.
> It is the 1-page "where are we" summary. Details live in the other files.
> For a new chat with an AI assistant, paste `MASTER_PROMPT.md` first.

## 1. What we are building (10 lines)

- **Event:** Bosch hackathon, 2 days, MIT Bengaluru (Oct 2026). Team of 5.
- **Problem:** "AI-Driven Chaos Engineering for Cloud-Native Resilience".
- **One-liner:** An AI system that studies a cloud-native app, decides what to break first and why, breaks it safely (with automatic stop), measures the damage and recovery, and writes a ranked report of weaknesses with concrete fixes and a resilience score.
- **Project name (working):** ChaosPilot
- **Target app:** OpenTelemetry Demo (open-source e-commerce microservices app) on Kubernetes. No shared VM yet: P1 and Joy each run a local **Docker Desktop Kubernetes (kubeadm)** cluster on Windows (D-021). Which machine is official is still open (O-6).
- **Fault tools:** Chaos Mesh (real infra faults: pod kill, network delay/loss, CPU stress) + the demo's built-in feature flags (app faults: payment failure, cart failure, etc.).
- **AI role:** deterministic risk ranking + LLM writes hypotheses and explanations. **The LLM never touches the cluster directly.** Policy engine validates every plan.
- **Approach:** "Trimmed hybrid" — must-have core (Tier 0) + cheap differentiators (Tier 1) + optional stretch (Tier 2).
- **Pitch line:** "Detect risks statically, prove them with safe chaos, recommend fixes as code, and show our ranking beats random."

## 2. File map

| File | What it holds | When to read |
|---|---|---|
| `MASTER_PROMPT.md` | Prompt that briefs a new AI chat (working style, rules, state, next task) | Paste first in a new chat |
| `CONTEXT.md` | This summary | Always first |
| `progress.md` | Live task board, gate status, blockers, log | Always second |
| `Plan.md` | Full plan: scope tiers, timeline, roles, gates, risks | Planning / re-planning |
| `Requirementsandsetup.md` | Requirements from the slides + setup commands (VM plan and the verified local Kubernetes setup, B11) | Setup, compliance checks |
| `Rules.md` | Team rules + rules for AI assistants | Before writing any code |
| `techstack.md` | Tools, libraries, versions, fallbacks | Before adding a dependency |
| `Architecture.md` | Components, data contracts (JSON), safety layers, scoring | Before building a module |
| `Experiments.md` | Fault catalog, hypotheses, results log | Running experiments |
| `Decisions.md` | Decision log (why we chose X) — do not re-debate these | When tempted to change direction |
| `Demo.md` | 3-minute video script + recording checklist | Day 2 afternoon |
| `P2_Observability.md` | P2 role brief: responsibilities, interfaces, sprints, §6 current state | When working as P2 |

## 3. Current phase

- **Phase:** see the top of `progress.md` (single source of truth for status).
- **Snapshot (11 Oct, 01:45 IST):**
  - **Repo:** `github.com/Lexd0o/AI_Driven_Chaos_Engineering` (owner is `Lexd0o`; older docs said `Lexd0` by mistake).
  - **P2 (Joy):** observability code done and tested (probes, sampler, Prometheus + Jaeger v3, evidence, dashboard v0, `collect_run_metrics()` + fake-shop dry run). Branch `p2-observability`.
  - **P1's chaos agent** (branch `chaos-agent`: Groq planner, validated Chaos Mesh manifests, guardian kill switch) was **ported into our repo as `src/chaos/`** on branch `integrate-chaos` (Stage 1: packaging + crash fix, all tests pass, preview against the cluster passes). Joy integrates (D-025).
  - **Joy's laptop now runs a local Kubernetes cluster** (Docker Desktop kubeadm, same as P1) with the OTel Demo (Helm) and Chaos Mesh; shop, Jaeger and Prometheus span metrics verified end to end (D-021).
  - **Next:** Stage 2 = wire the guardian into the controller and connect each run to P2's sampler and run metrics (one experiment id, one recovery definition). Until then **do not run `controller --execute`**.
- **Notable findings:** default checkout memory limit made checkout very slow on Docker Compose (D-020); P1's live 10 % packet loss on product-catalog (30 s, 1 replica) caused no visible user impact — a stronger fault is needed for the breach test (G3).
- **Open questions:** see "Open questions" in `progress.md`, and O-6 / O-7 in `Decisions.md`.

## 4. Non-negotiables (short version of Rules.md)

1. The LLM proposes; the policy validates; the executor acts. No LLM shell access.
2. Only one experiment runs at a time (experiment lock).
3. Every fault has a hard duration cap and a working stop command.
4. Never target `kube-system`, `chaos-mesh`, or the monitoring components.
5. Scores are computed by code, not by the LLM. The LLM only explains.
6. Code style: flat, simple Python functions; no class hierarchies.
7. Feature freeze at T−3h before submission. No exceptions.
8. Update `progress.md` after finishing any task.
9. Never invent numbers. Local or fake data is never committed or reported; `data/` stays out of git.

## 5. How to hand off to a new model

Paste: `MASTER_PROMPT.md` + `CONTEXT.md` + `progress.md` + `Decisions.md` + the one file relevant to your task (e.g. `Architecture.md` for coding, `P2_Observability.md` for P2 work). Then state: "I am P<n>, working on <task id>." Attach the current code files you want changed.
