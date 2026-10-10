# Application map

Generated 2026-10-10T09:52:26.154833+00:00 (namespace otel-demo, Jaeger lookback 1h).

```mermaid
graph LR
    ad["ad"] -->|5| flagd["flagd"]
    cart["cart"] -->|204| flagd["flagd"]
    checkout["checkout"] -->|210| cart["cart"]
    checkout["checkout"] -->|297| currency["currency"]
    checkout["checkout"] -->|105| email["email"]
    checkout["checkout"] -->|105| payment["payment"]
    checkout["checkout"] -->|192| product_catalog["product-catalog"]
    checkout["checkout"] -->|210| shipping["shipping"]
    fraud_detection["fraud-detection"] -->|5| flagd["flagd"]
    frontend["frontend"] -->|449| ad["ad"]
    frontend["frontend"] -->|1814| cart["cart"]
    frontend["frontend"] -->|105| checkout["checkout"]
    frontend["frontend"] -->|837| currency["currency"]
    frontend["frontend"] -->|5320| product_catalog["product-catalog"]
    frontend["frontend"] -->|926| recommendation["recommendation"]
    frontend["frontend"] -->|146| shipping["shipping"]
    frontend_proxy["frontend-proxy"] -->|522| flagd["flagd"]
    frontend_proxy["frontend-proxy"] -->|13169| frontend["frontend"]
    frontend_proxy["frontend-proxy"] -->|4639| image_provider["image-provider"]
    frontend_web["frontend-web"] -->|5537| frontend_proxy["frontend-proxy"]
    payment["payment"] -->|15| flagd["flagd"]
    recommendation["recommendation"] -->|5| flagd["flagd"]
    recommendation["recommendation"] -->|926| product_catalog["product-catalog"]
    shipping["shipping"] -->|251| quote["quote"]
```

| Service | Kind | Ready | Upstream | Downstream |
|---|---|---|---|---|
| accounting | deployment | 1/1 | - | - |
| ad | deployment | 1/1 | frontend | flagd |
| astronomy-db | deployment | 1/1 | - | - |
| cart | deployment | 1/1 | checkout, frontend | flagd |
| checkout | deployment | 1/1 | frontend | cart, currency, email, payment, product-catalog, shipping |
| currency | deployment | 1/1 | checkout, frontend | - |
| email | deployment | 1/1 | checkout | - |
| flagd | deployment | 1/1 | ad, cart, fraud-detection, frontend-proxy, payment, recommendation | - |
| fraud-detection | deployment | 1/1 | - | flagd |
| frontend | deployment | 1/1 | frontend-proxy | ad, cart, checkout, currency, product-catalog, recommendation, shipping |
| frontend-proxy | deployment | 1/1 | frontend-web | flagd, frontend, image-provider |
| frontend-web | trace-only | - | - | frontend-proxy |
| grafana | deployment | 1/1 | - | - |
| image-provider | deployment | 1/1 | frontend-proxy | - |
| jaeger | deployment | 1/1 | - | - |
| kafka | deployment | 1/1 | - | - |
| load-generator | deployment | 1/1 | - | - |
| opamp-server | deployment | 1/1 | - | - |
| opensearch | statefulset | 1/1 | - | - |
| opensearch-headless | service_only | - | - | - |
| otel-collector | service_only | - | - | - |
| payment | deployment | 1/1 | checkout | flagd |
| product-catalog | deployment | 1/1 | checkout, frontend, recommendation | - |
| prometheus | deployment | 1/1 | - | - |
| quote | deployment | 1/1 | shipping | - |
| recommendation | deployment | 1/1 | frontend | flagd, product-catalog |
| shipping | deployment | 1/1 | checkout, frontend | quote |
| telemetry-docs | deployment | 1/1 | - | - |
| valkey-cart | deployment | 1/1 | - | - |
