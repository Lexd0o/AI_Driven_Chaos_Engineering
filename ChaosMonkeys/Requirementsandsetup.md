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

For Prometheus API access: the Helm chart's service is `prometheus` on port 9090 (confirmed on the local Docker Desktop cluster, B11): `kubectl -n otel-demo port-forward svc/prometheus 9090:9090`.

## B5.1 Telemetry endpoints and metric names (confirmed, P2)

Confirmed on Joy's laptop (Docker Compose demo on 10 Oct; Prometheus + Jaeger UI also on the local Kubernetes cluster on 11 Oct). Paths are relative to the frontend-proxy URL.

| What | Where | Notes |
|---|---|---|
| Web store API | `GET /api/products` · `POST /api/cart?currencyCode=USD` · `GET /api/cart?sessionId=<user>&currencyCode=USD` · `POST /api/checkout?currencyCode=USD` | Used by P2 journey probes; product id `OLJCESPC7Z` |
| Jaeger UI | `/jaeger/ui` | |
| Jaeger API (v3, OTLP JSON) | `/jaeger/ui/api/v3/services`, `/jaeger/ui/api/v3/traces` | Params `query.service_name`, `query.start_time_min`, `query.start_time_max` (RFC3339), `query.num_traces`. v1 paths return 404 (D-017). 404 "No traces found" = empty. [verify] v3 through the cluster's frontend-proxy |
| Prometheus API | `<prometheus>:9090/api/v1/query`, `/query_range`, `/label/__name__/values` | In the Helm chart, Prometheus receives metrics by OTLP push from the collector (no scrape targets) |
| Span metrics | `traces_span_metrics_calls_total`, `traces_span_metrics_duration_milliseconds_{bucket,count,sum}` | Labels `service_name`, `span_kind`, `span_name`, `status_code`, `service_criticality`. Use `span_kind="SPAN_KIND_SERVER"` and `[2m]` rate windows (D-016). No error series when no errors (treat as 0) |
| Error-rate example | `sum(rate(traces_span_metrics_calls_total{service_name="product-catalog",span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}[2m])) / sum(rate(traces_span_metrics_calls_total{service_name="product-catalog",span_kind="SPAN_KIND_SERVER"}[2m]))` | |
| Load generator stats | `/loadgen/stats/requests` | Load generator is scaled to 0 on Joy's local cluster (D-021) |

curl in PowerShell: use `curl.exe` (not the `curl` alias) and add `-g` when a URL contains `[2m]`, otherwise curl reads the brackets as a range ("bad range").

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

Current reality (11 Oct, branch `integrate-chaos`): P2 code in `src/` (`telemetry`, `probe`, `observe`, `baseline`, `evidence`, `runmetrics`), `dashboard/app.py`, `tools/`, `tests/`, `config/journeys.yaml`; P1's agent in `src/chaos/` with inputs `config/chaos_settings.json`, `config/experiment.json`, `config/service_snapshot.json`, `config/app_map_overrides.json` and outputs in `data/chaos/` (D-025). No Makefile yet.

## B11. Local Kubernetes on Windows (Docker Desktop) — verified on Joy's laptop, 11 Oct

Same setup as P1 (D-021). PowerShell, any folder, no venv. Use PowerShell, not Git Bash: Git Bash rewrote Chaos Mesh's Docker socket path into a Windows path on P1's machine.

1. **Kubernetes:** Docker Desktop → Settings → Kubernetes → Enable, provisioning method **Kubeadm** (not kind) → Apply. Check:
   ```powershell
   kubectl config use-context docker-desktop
   kubectl get nodes -o wide        # node docker-desktop, Ready, runtime docker://...
   ```
2. **Helm:** `winget install Helm.Helm`, then open a new terminal; `helm version`.
3. **OTel Demo** (OpenSearch off to save memory):
   ```powershell
   helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
   helm repo update
   helm install otel-demo open-telemetry/opentelemetry-demo -n otel-demo --create-namespace --set opensearch.enabled=false
   ```
   First image pulls took about 35 min on Joy's laptop.
4. **Fixes needed on Docker Desktop:**
   - Collector `otel-collector-agent` fails with "path / is mounted on / but it is not a shared or slave mount" → turn off host metrics:
     `helm upgrade otel-demo open-telemetry/opentelemetry-demo -n otel-demo --reuse-values --set opentelemetry-collector.presets.hostMetrics.enabled=false`
   - Pods `Pending` with "Insufficient memory" (node ~7.7 GB, 99 % reserved) → scale extras to 0 (re-run after every `helm upgrade`, which brings them back):
     `kubectl -n otel-demo scale deploy load-generator mcp chatbot agent grafana accounting fraud-detection --replicas=0`
     Kept on purpose: `quote` (shipping calls it), `kafka` (checkout uses it).
   - Collector memory headroom: `helm upgrade otel-demo open-telemetry/opentelemetry-demo -n otel-demo --reuse-values --set opentelemetry-collector.resources.limits.memory=900Mi`
   - Collector restarts during heavy image pulls are liveness-probe timeouts, not crashes; if span metrics stop, `kubectl -n otel-demo rollout restart daemonset otel-collector-agent`.
   - Collector logs full of `opensearch` export errors are expected (OpenSearch is off) and harmless.
5. **Chaos Mesh** (Docker runtime, 1 controller replica, no dashboard) [verify chart version with `helm list -A`]:
   ```powershell
   helm repo add chaos-mesh https://charts.chaos-mesh.org
   helm repo update
   helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace --set chaosDaemon.runtime=docker --set chaosDaemon.socketPath=/var/run/docker.sock --set controllerManager.replicaCount=1 --set dashboard.create=false
   kubectl -n chaos-mesh get pods            # controller-manager, daemon, dns-server Running
   kubectl get crd | Select-String "chaos-mesh"
   ```
6. **Access** (each in its own terminal, keep open): `kubectl -n otel-demo port-forward svc/frontend-proxy 8081:8080` and `kubectl -n otel-demo port-forward svc/prometheus 9090:9090`. Port 8081 because an Oracle listener holds 8080 on Joy's laptop. The Prometheus port-forward must print both `127.0.0.1` and `[::1]`; if one is missing, another process holds the port (D-026).
7. **Health check:**
   ```powershell
   kubectl -n otel-demo get pods                                      # all 1/1 Running, restarts stable
   curl.exe -s -o NUL -w "%{http_code}" http://localhost:8081/        # 200
   curl.exe -sSg 'http://127.0.0.1:9090/api/v1/query?query=sum(rate(traces_span_metrics_calls_total[2m]))'   # value > 0
   ```
   Jaeger UI: `http://localhost:8081/jaeger/ui` → service `frontend` → Find Traces.
8. **Before using the cluster,** stop anything else on 8081/9090: `docker ps --format "table {{.Names}}\t{{.Ports}}"` — old Compose containers (`prometheus`, `jaeger`, `grafana`, `opensearch`, `opamp-server`) must be stopped with `docker stop ...`. Never stop containers whose names start with `k8s_`.

