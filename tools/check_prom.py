from src.telemetry import load_config, prom_query
from src import observe

cfg = load_config()
print("has sample_services:", hasattr(observe, "sample_services"))
print("service_queries in config:", list(cfg.get("service_queries", {})))
for name, q in cfg.get("service_queries", {}).items():
    try:
        r = prom_query(q)
        print(name, "->", len(r), "series", [(s["metric"].get("service_name"), s["value"][1]) for s in r[:3]])
    except Exception as e:
        print(name, "ERROR", e)
