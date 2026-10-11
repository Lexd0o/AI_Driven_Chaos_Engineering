from src.telemetry import prom_query

QUERIES = {
    "any span-metric series": 'count(traces_span_metrics_calls_total)',
    "span kinds": 'count by (span_kind) (traces_span_metrics_calls_total)',
    "status codes": 'count by (status_code) (traces_span_metrics_calls_total)',
    "newest sample age (s)": 'min(time() - timestamp(traces_span_metrics_calls_total))',
    "rate 1m, all spans": 'count(rate(traces_span_metrics_calls_total[1m]))',
    "rate 5m, SERVER spans": 'count(rate(traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER"}[5m]))',
}
for name, q in QUERIES.items():
    try:
        r = prom_query(q)
        print(name, "->", [(s["metric"], s["value"][1]) for s in r])
    except Exception as e:
        print(name, "ERROR", e)