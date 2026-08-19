#!/usr/bin/env python3
"""
test_publish_webhook.py — the fire-and-forget publish webhook.

Isolated from test_publish.py: proves ONLY the new behaviour, so the existing
publish suite stays untouched. DB helpers are monkeypatched (no database); a real
local HTTP receiver runs in a background thread to capture the POST. Proves:

  1. On publish, an event is POSTed to PUBLISH_WEBHOOK_URL with the tender's
     MPANs, the dashboard link, and the bearer secret header.
  2. With the env unset, publish still succeeds and the webhook is skipped
     (local dev + the existing tests are unaffected).
  3. If the endpoint fails (non-2xx / unreachable), the publish STILL succeeds —
     the webhook can never break a publish.

Run from the repo root:  python3 tests/test_publish_webhook.py
Prints 'ALL WEBHOOK CHECKS PASSED' and exits 0 when green.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


TID = "22222222-2222-4222-8222-222222222222"

# A two-site tender (like the real Pizzarova tender: two meters).
def _tender(**over):
    t = {
        "id": TID, "client_name": "Pizzarova", "tender_label": "Electricity — Aug 2026",
        "utility": "electricity", "status": "draft", "version": 1,
        "created_at": "2026-08-18T09:00:00Z", "created_by": "rory@rye.energy",
        "expires_at": "2026-09-30",
        "sites": [
            {"mpxn": "2200017055132", "site_name": "Gloucester Road", "eac": 38089.0, "eac_source": "quote"},
            {"mpxn": "2200043435566", "site_name": "North Street", "eac": 33680.0, "eac_source": "quote"},
        ],
        "quotes": [{"supplier": "Yu Energy", "term": "12m", "featured": True,
                    "lines": [{"mpxn": "2200017055132", "unitRate": 25.41},
                              {"mpxn": "2200043435566", "unitRate": 23.778}]}],
    }
    t.update(over)
    return t


class _Receiver(BaseHTTPRequestHandler):
    """Captures one POST into the class-level `last` dict, replies with `reply_status`."""
    last = None
    reply_status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = None
        _Receiver.last = {"body": body, "api_key": self.headers.get("X-Workflow-Api-Key"),
                          "content_type": self.headers.get("Content-Type"),
                          "user_agent": self.headers.get("User-Agent")}
        self.send_response(_Receiver.reply_status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # keep the test output clean
        pass


def _start_server():
    srv = HTTPServer(("127.0.0.1", 0), _Receiver)  # port 0 = OS picks a free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _publish(client):
    main._get_tender = lambda tid, v=None: _tender() if tid == TID else None  # type: ignore
    return client.post("/api/publish", json={"tender_id": TID})


def main_test():
    client = TestClient(main.app)
    store = {}
    main._next_version = lambda tid: 2                        # type: ignore
    main._write_tender = lambda t: store.update(latest=dict(t))  # type: ignore

    srv, port = _start_server()
    secret = "test-secret-abc123"

    try:
        print("1) publish fires the webhook with the tender's MPANs + secret")
        _Receiver.last = None
        _Receiver.reply_status = 200
        os.environ["PUBLISH_WEBHOOK_URL"] = f"http://127.0.0.1:{port}/hook"
        os.environ["PUBLISH_WEBHOOK_SECRET"] = secret
        r = _publish(client)
        check("publish -> 200", r.status_code == 200)
        wh = r.json().get("webhook", {})
        check("response reports webhook fired", wh.get("fired") is True and wh.get("status") == 200)
        got = _Receiver.last
        check("receiver got a POST", got is not None)
        body = (got or {}).get("body") or {}
        check("event == published", body.get("event") == "published")
        check("both MPANs sent, in site order",
              body.get("mpxns") == ["2200017055132", "2200043435566"])
        check("dashboard_url included", "/d/pizzarova/" in (body.get("dashboard_url") or ""))
        check("tender_id + version included", body.get("tender_id") == TID and body.get("version") == 2)
        check("X-Workflow-Api-Key header sent (Retool auth)", (got or {}).get("api_key") == secret)
        check("content-type json", (got or {}).get("content_type") == "application/json")
        # Regression: must NOT send the default urllib UA (Cloudflare 403s it).
        ua = (got or {}).get("user_agent") or ""
        check("real User-Agent sent, not Python-urllib", "RYE-tenderApp" in ua and "urllib" not in ua)

        print("2) with the env unset, publish still succeeds and the hook is skipped")
        os.environ.pop("PUBLISH_WEBHOOK_URL", None)
        os.environ.pop("PUBLISH_WEBHOOK_SECRET", None)
        _Receiver.last = None
        r2 = _publish(client)
        check("publish -> 200 (unconfigured)", r2.status_code == 200)
        check("webhook reported skipped", r2.json().get("webhook", {}).get("fired") is False)
        check("no POST was made", _Receiver.last is None)

        print("3) a failing endpoint does NOT break the publish")
        _Receiver.reply_status = 500
        os.environ["PUBLISH_WEBHOOK_URL"] = f"http://127.0.0.1:{port}/hook"
        r3 = _publish(client)
        check("publish still -> 200 despite 500", r3.status_code == 200)
        check("webhook reported not-fired with an error",
              r3.json().get("webhook", {}).get("fired") is False
              and "error" in r3.json().get("webhook", {}))

        print("4) an unreachable endpoint does NOT break the publish")
        os.environ["PUBLISH_WEBHOOK_URL"] = "http://127.0.0.1:9/hook"  # nothing listening
        r4 = _publish(client)
        check("publish still -> 200 despite connection error", r4.status_code == 200)
        check("webhook reported an error, not raised", r4.json().get("webhook", {}).get("fired") is False)

        print("5) a MALFORMED url does NOT break the publish (regression: Request() must be guarded)")
        # A value with no scheme makes urllib.request.Request() raise ValueError.
        # This once 500'd publish because Request() sat outside the try/except.
        os.environ["PUBLISH_WEBHOOK_URL"] = "PUBLISH_WEBHOOK_SECRET"  # the real-world mixup
        r5 = _publish(client)
        check("publish still -> 200 despite malformed url", r5.status_code == 200)
        check("webhook reported a ValueError, not raised",
              r5.json().get("webhook", {}).get("fired") is False
              and "ValueError" in (r5.json().get("webhook", {}).get("error") or ""))

        print("6) /api/webhook-check reports config and can test-fire (the debug endpoint)")
        os.environ.pop("PUBLISH_WEBHOOK_URL", None)
        os.environ.pop("PUBLISH_WEBHOOK_SECRET", None)
        rc = client.get("/api/webhook-check")
        cj = rc.json()
        check("check -> 200, url_set false when unset", rc.status_code == 200 and cj.get("url_set") is False)
        check("check reports secret_set false when unset", cj.get("secret_set") is False)
        # now configure + test-fire against the live local receiver
        os.environ["PUBLISH_WEBHOOK_URL"] = f"http://127.0.0.1:{port}/hook"
        os.environ["PUBLISH_WEBHOOK_SECRET"] = secret
        _Receiver.reply_status = 200
        _Receiver.last = None
        rc2 = client.get("/api/webhook-check?test=true")
        cj2 = rc2.json()
        check("check reports url_set true + host", cj2.get("url_set") is True and "127.0.0.1" in (cj2.get("url_host") or ""))
        check("check test_fire fired -> 200", cj2.get("test_fire", {}).get("fired") is True and cj2["test_fire"].get("status") == 200)
        check("test event received, empty mpxns (safe no-op)",
              (_Receiver.last or {}).get("body", {}).get("event") == "webhook_test"
              and (_Receiver.last or {}).get("body", {}).get("mpxns") == [])
        check("test-fire sent the api key header", (_Receiver.last or {}).get("api_key") == secret)
    finally:
        os.environ.pop("PUBLISH_WEBHOOK_URL", None)
        os.environ.pop("PUBLISH_WEBHOOK_SECRET", None)
        srv.shutdown()

    if FAILURES:
        print(f"\n{len(FAILURES)} CHECK(S) FAILED")
        return 1
    print("\nALL WEBHOOK CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
