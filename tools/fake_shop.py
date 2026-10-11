"""Fake shop for testing probes without the cluster.  python tools/fake_shop.py --port 8080 --fail-checkout 0.0
Change failure live: GET /_fail?checkout=0.5  (sets 50% checkout failures)."""
import argparse, json, random, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

FAIL = {"checkout": 0.0, "cart": 0.0, "browse": 0.0}
DELAY = {"extra_ms": 0}

class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _handle(self):
        u = urlparse(self.path); time.sleep(random.uniform(0.02, 0.1) + DELAY["extra_ms"] / 1000)
        if u.path == "/_fail":
            q = parse_qs(u.query)
            for k in FAIL:
                if k in q: FAIL[k] = float(q[k][0])
            if "delay_ms" in q: DELAY["extra_ms"] = int(q["delay_ms"][0])
            return self._send(200, {"fail": FAIL, "delay": DELAY})
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        key = {"/api/products": "browse", "/api/cart": "cart", "/api/checkout": "checkout"}.get(u.path)
        if key is None: return self._send(404, {"error": "not found"})
        if random.random() < FAIL[key]: return self._send(500, {"error": "injected"})
        self._send(200, {"ok": True})
    do_GET = do_POST = _handle
    def log_message(self, *a): pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--fail-checkout", type=float, default=0.0); a = ap.parse_args()
    FAIL["checkout"] = a.fail_checkout
    print(f"fake shop on :{a.port}"); ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()