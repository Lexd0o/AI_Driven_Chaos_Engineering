# Requirementsandsetup.md

Part A = what the judges require (from the Bosch slides). Part B = how to set up the environment.

---

# PART A — Requirements

## A1. Problem statement (from slides, paraphrased)

Build an AI system that intelligently introduces controlled failures into a cloud-native application environment and analyses how the system responds, to improve its resilience. The AI decides **what to break, when, and how safely**, then explains what the failure revealed and how to fix it. Teams choose their own free, open-source, cloud-native sample app from a public GitHub repo and run it in their own sandbox.

## A2. Functional requirements

| ID | Requirement (slide) | Our implementation | Tier |
|---|---|---|---|
| FR-1 | Select and understand a sample app; map services, dependencies, data flows (auto or semi-auto) | OTel Demo; `discover.py` (k8s API + Jaeger) + `graph_overrides.yaml` | 0 |
| FR-2 | Plan failure experiments intelligently — most informative/riskiest first, based on dependency map and observed behaviour | `audit.py` + `rank.py` + `plan.py` (LLM, validated JSON, reason per item) | 0 |
| FR-3 | Inject controlled failures with safety limits; stop automatically if impact exceeds thresholds (error rate, latency, blast radius) | `inject.py` + `guard.py` + duration caps + `stop-all` | 0 |
| FR-4 | Observe behaviour: capture errors, slowdowns, recovery time | `observe.py` (Prometheus + journey probe + pod status) → SQLite | 0 |
| FR-5 | Analyse and report: identify weaknesses, recommend fixes, ideally a ranked resilience score | `score.py` + `report.py` + `fixes.py` | 0 |
| FR-6 | ≥3 failure types run; errors, slowdowns, recovery time recorded | Experiments.md catalog | 0 |
| FR-7 | Automatic stop or rollback on threshold breach, tested at least once | Deliberate breach test (Day 1, S3) | 0 |

Stretch (optional on slides): re-run after a fix (T2.1), live dashboard (T1.5), combined failures (T2.4).

## A3. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Reproducible: README lets a stranger set up and run it; "clean run from scratch" before submission |
| NFR-2 | Safe: no experiment outside the app namespace; every fault stoppable and time-capped |
| NFR-3 | Explainable: every AI decision has a written reason; every finding cites run evidence |
| NFR-4 | Deterministic core: ranking and scoring reproducible without the LLM |
| NFR-5 | Honest reporting: coverage stated; illustrative numbers labelled; inconclusive runs not scored |
| NFR-6 | Demo-ready: whole Day-2 flow runnable from one command (`make demo`) |

## A4. Deliverables (submitted through a single form/portal)

| # | Deliverable | Spec from slide | Owner |
|---|---|---|---|
| 1 | Source code | Public or shared repo link with README covering setup and how to run | P1 |
| 2 | Demo video | ≤3 minutes: system injecting a failure, observing impact, reporting findings | P5 |
| 3 | Architecture overview | One page or slide: components, how the AI makes decisions, safety controls | P5 |
| 4 | Findings report | Sample output from sandbox app: weaknesses found, ranked, with recommended fixes | P5 |
| 5 | Team form | Team name, members, tools and AI models used, third-party libraries and data used | P5 |

## A5. Milestones (guidance checkpoints, not scoring gates)

- **Day 1:** setup & app selection · application mapping · monitoring baseline · first failure injection · safety controls tested · Day-1 checkpoint demo.
- **Day 2:** AI experiment planner · multiple experiments (≥3 types) · analysis & scoring · feature freeze & polish · demo video & documents · submission (aim 30 min early).

---

# PART B — Setup

> Commands marked **[verify]** are from documentation or memory but untested on our VM. Confirm on Day 1 and fix this file.

## B1. VM sizing (team estimate)

| Resource | Minimum | Why |
|---|---|---|
| vCPU | 8 | OTel Demo + Kafka + Chaos Mesh + Prometheus/Jaeger/Grafana |
| RAM | 16 GB | OTel Demo docs require 6 GB free for the app alone |
| Disk | 60 GB | Container images |
| OS | Ubuntu 22.04/24.04 LTS | k3s support; `sch_netem` usually present |
| Network | Outbound internet to container registries, Helm repos, GitHub, LLM API | Check first |

VM details (fill in): provider: `____` · IP: `____` · user: `____` · specs: `____`

## B2. Pre-flight checks (P1, first 30 min)

```bash
# Egress
curl -sI https://ghcr.io | head -1
curl -sI https://open-telemetry.github.io/opentelemetry-helm-charts/index.yaml | head -1
curl -sI https://charts.chaos-mesh.org/index.yaml | head -1
# Kernel module for network delay/loss
sudo modprobe sch_netem && lsmod | grep sch_netem
# Resources
nproc; free -h; df -h /
```

## B3. Install k3s + Helm

```bash
curl -sfL https://get.k3s.io | sh -
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
mkdir -p ~/.kube && cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
kubectl get nodes

curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

## B4. Install the OpenTelemetry Demo (pin the chart version!)

```bash
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update
helm search repo open-telemetry/opentelemetry-demo --versions | head -5   # pick one, record it in techstack.md
kubectl create ns otel-demo
helm install otel-demo open-telemetry/opentelemetry-demo -n otel-demo --version <PINNED>
kubectl -n otel-demo get pods -w
```

- The chart does **not** support upgrades. To change version: `helm uninstall` then install fresh.
- Record the actual deployment names: `kubectl -n otel-demo get deploy` → paste into `Experiments.md` §1. **[verify]** label key used by the chart (likely `app.kubernetes.io/component`).

## B5. Access for the team (SSH tunnel)

On the VM:
```bash
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080
```
On each laptop:
```bash
ssh -N -L 8080:localhost:8080 <user>@<vm-ip>
```
Then open: web store `http://localhost:8080/` · Grafana `/grafana/` · load generator `/loadgen/` · Jaeger `/jaeger/ui/` · flag editor `/feature`.

For Prometheus API access: **[verify]** service name with `kubectl -n otel-demo get svc | grep -i prom`, then port-forward it (e.g. 9090).

## B6. Install Chaos Mesh (k3s / containerd)

```bash
helm repo add chaos-mesh https://charts.chaos-mesh.org
kubectl create ns chaos-mesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --version 2.8.4 \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
  --set controllerManager.enableFilterNamespace=true          # [verify] flag names on the K3s tab of the install docs
kubectl annotate ns otel-demo chaos-mesh.org/inject=enabled    # [verify] namespace-filter annotation
kubectl -n chaos-mesh get pods
```

Smoke test (G1):
```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata: {name: smoke-podkill, namespace: otel-demo}
spec:
  action: pod-kill
  mode: one
  selector:
    namespaces: [otel-demo]
    labelSelectors: {"app.kubernetes.io/component": "ad"}   # [verify] label
EOF
kubectl -n otel-demo get pods -w     # ad pod should restart
kubectl -n otel-demo delete podchaos smoke-podkill
```

## B7. Feature flags (flagd)

- UI: `http://localhost:8080/feature` (basic + advanced JSON views).
- Programmatic toggle: **[verify] Day 1 (P4)** — options in order of preference:
  1. Write path used by the flag UI (inspect browser network tab).
  2. Patch the flagd ConfigMap and wait for propagation (measure delay!).
- Record the working method and its flip latency here: `____`

## B8. Python environment (controller runs on the VM)

```bash
sudo apt-get install -y python3-venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # LLM_API_KEY, PROM_URL, JAEGER_URL, NAMESPACE=otel-demo
```

## B9. Snapshot & recovery

- After B4 + B6 healthy → take a VM snapshot (if provider allows). Name: `healthy-v1`.
- `scripts/bootstrap_vm.sh` must reproduce B3–B8 unattended. Target: < 20 min.

## B10. Repo layout

```
chaospilot/
├── CONTEXT.md Plan.md progress.md Requirementsandsetup.md Rules.md techstack.md
├── Architecture.md Experiments.md Decisions.md Demo.md README.md
├── Makefile                 # up, baseline, discover, audit, plan, run, report, dashboard, stop-all, demo
├── requirements.txt  .env.example
├── config/  policy.yaml  criticality.yaml  fault_catalog.yaml  graph_overrides.yaml
├── src/     common.py discover.py audit.py rank.py plan.py policy.py inject.py
│            observe.py guard.py run.py score.py report.py fixes.py benchmark.py
├── dashboard/app.py
├── scripts/ bootstrap_vm.sh install_otel.sh install_chaos_mesh.sh stop_all.sh
├── fixtures/                # recorded real outputs for offline dev
├── data/    graph.json risks.json plan.json baseline.json runs.db
└── reports/ findings_report.md fixes/*.yaml
```
