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
                          "content_type": self.headers.get("Content-Type")}
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
