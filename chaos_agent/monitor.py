import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

PROBE_PATH = "frontend-proxy:8080/proxy/api/products"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_settings():
    return json.loads(
        (BASE_DIR / "settings.json").read_text(encoding="utf-8")
    )


# Run a fixed kubectl command and return its stdout.
def kubectl(settings, arguments, timeout=30, namespaced=True):
    command = ["kubectl", "--context", settings["context"]]

    if namespaced:
        command += ["--namespace", settings["namespace"]]

    result = subprocess.run(
        [*command, *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "kubectl command failed."
        )

    return result.stdout


# Call an in-cluster HTTP service through the API-server proxy.
def proxy_get(settings, service_port, path, timeout=30):
    raw = (
        f"/api/v1/namespaces/{settings['namespace']}"
        f"/services/{service_port}/proxy/{path}"
    )

    return kubectl(
        settings, ["get", "--raw", raw],
        timeout=timeout, namespaced=False,
    )


def prometheus_query(settings, query):
    path = "api/v1/query?query=" + urllib.parse.quote(query, safe="")
    body = json.loads(proxy_get(settings, "prometheus:9090", path))

    if body.get("status") != "success":
        raise RuntimeError("Prometheus query failed.")

    results = body["data"]["result"]

    if not results:
        return None

    value = float(results[0]["value"][1])

    # NaN means no traffic in the window.
    return None if value != value else value


def prometheus_signals(settings, service):
    selector = (
        f'service_name="{service}",span_kind="SPAN_KIND_SERVER"'
    )
    calls = "traces_span_metrics_calls_total"
    window = "[2m]"

    rps = prometheus_query(
        settings,
        f"sum(rate({calls}{{{selector}}}{window}))",
    )

    errors = prometheus_query(
        settings,
        f'sum(rate({calls}{{{selector},'
        f'status_code="STATUS_CODE_ERROR"}}{window}))',
    )

    p95 = prometheus_query(
        settings,
        "histogram_quantile(0.95, sum by (le)(rate("
        f"traces_span_metrics_duration_milliseconds_bucket"
        f"{{{selector}}}{window})))",
    )

    # Empty numerator means no errors were recorded.
    error_ratio = None

    if rps:
        error_ratio = (errors or 0.0) / rps

    return {
        "error_ratio": error_ratio,
        "p95_ms": p95,
        "rps": rps,
    }


# One synthetic request to the storefront product list.
def probe(settings, timeout=5):
    started = time.monotonic()
    status = None
    ok = False

    try:
        raw = (
            f"/api/v1/namespaces/{settings['namespace']}"
            f"/services/{PROBE_PATH}"
        )

        result = subprocess.run(
            [
                "kubectl", "--context", settings["context"],
                "get", "--raw", raw,
                f"--request-timeout={timeout}s",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout + 2,
        )

        if result.returncode == 0:
            json.loads(result.stdout)
            ok = True
            status = 200

    except (subprocess.TimeoutExpired, ValueError):
        pass

    return {
        "ok": ok,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "status": status,
    }


def pod_health(settings, deployment):
    data = json.loads(
        kubectl(settings, ["get", "deployment", deployment, "-o", "json"])
    )

    desired = data["spec"].get("replicas", 1)
    ready = data.get("status", {}).get("readyReplicas", 0)
    labels = data["spec"]["selector"].get("matchLabels", {})
    selector = ",".join(f"{k}={v}" for k, v in labels.items())

    pods = json.loads(
        kubectl(settings, ["get", "pods", "-l", selector, "-o", "json"])
    )

    restarts = sum(
        status.get("restartCount", 0)
        for pod in pods["items"]
        for status in pod["status"].get("containerStatuses", [])
    )

    return {
        "desired": desired,
        "ready": ready,
        "restarts": restarts,
        "pods": len(pods["items"]),
        "fully_ready": desired >= 1 and ready >= desired,
    }


def percentile(values, fraction):
    if not values:
        return None

    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))

    return ordered[index]


# Collect probes, Prometheus signals and pod health for a period.
def capture_baseline(settings, service, deployment, seconds=60, interval=2):
    probes = []
    end = time.monotonic() + seconds

    while time.monotonic() < end:
        tick = time.monotonic()
        probes.append(probe(settings))
        time.sleep(max(0, interval - (time.monotonic() - tick)))

    good = [item["latency_ms"] for item in probes if item["ok"]]

    return {
        "captured_at": utc_now(),
        "seconds": seconds,
        "probe_count": len(probes),
        "probe_success_rate": (
            round(len(good) / len(probes), 3) if probes else None
        ),
        "probe_p50_ms": round(statistics.median(good), 1) if good else None,
        "probe_p95_ms": percentile(good, 0.95),
        "prometheus": prometheus_signals(settings, service),
        "pod_health": pod_health(settings, deployment),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=int, metavar="SECONDS")
    args = parser.parse_args()

    settings = load_settings()
    service, deployment = next(iter(settings["targets"].items()))

    if args.baseline:
        baseline = capture_baseline(
            settings, deployment, deployment, args.baseline
        )

        (BASE_DIR / "baseline.json").write_text(
            json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )

        print(json.dumps(baseline, indent=2))
        print("Saved to baseline.json.")
        return

    print(json.dumps(probe(settings)))
    print(json.dumps(prometheus_signals(settings, deployment)))
    print(json.dumps(pod_health(settings, deployment)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        main()
    except Exception as error:
        raise SystemExit(f"Monitor failed: {error}")
