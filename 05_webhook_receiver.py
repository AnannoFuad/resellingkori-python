#!/usr/bin/env python3
"""
Example 5 - receive ResellingKori's webhooks (order status changes). Standard library only, nothing to install.

  Try it on your computer:
    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 05_webhook_receiver.py          (listens on 127.0.0.1:8099)

  In production run it behind a reverse proxy that serves https on your own domain, for example
    https://api.example.com/resellingkori/webhook   ->   http://127.0.0.1:8099/resellingkori/webhook
  and register that address with 06_register_webhook.py. Use any path you like (RK_WEBHOOK_PATH), but no "?query".

For every request the receiver
  1. accepts POST on the webhook path only and reads the RAW body (never parse and re-serialise the JSON first),
  2. verifies the key, the time (+-300 s) and the signature - a request that fails is answered 401/400 and ignored,
  3. answers the registration challenge ("callback.verify") with the proof,
  4. ignores a repeated nonce or an event it has already processed (it still answers 200, so that no retry follows),
  5. ignores an event whose "sequence" is not higher than the one already applied for that order,
  6. stores the event and answers 200 quickly.

Do the real work (update your shop's order, e-mail your customer) in a worker that reads what step 6 stored:
ResellingKori waits at most 10 seconds for your answer. Any 2xx counts as delivered; network errors, 5xx, 408, 425
and 429 are retried after 1, 3, 9, 27 and 81 minutes; other 4xx are not retried.

Stored in <data folder> (default ./rk-data): orders/<id>.json = latest state of each order, webhook-events.ndjson =
one line per applied event, seen/ = the nonces and event ids already handled (old ones are removed after a day).
The lock below protects ONE process. With several processes or servers keep the "seen" and "sequence" bookkeeping in
your database inside a transaction instead.
"""
import hashlib
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402

KEY_ID = os.environ.get("RK_KEY_ID")
SECRET = os.environ.get("RK_SECRET")
DATA = os.environ.get("RK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "rk-data")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8099"))
HOOK_PATH = os.environ.get("RK_WEBHOOK_PATH", "/resellingkori/webhook")
MAX_BODY = 262144
LOCK = threading.Lock()


def sha(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def first_time(kind, value):
    """True the first time a value is seen, false every later time. Atomic: the file is created exclusively."""
    try:
        os.close(os.open(os.path.join(DATA, "seen", "%s-%s" % (kind, sha(value))), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False


def already_done(event_id):
    return os.path.exists(os.path.join(DATA, "seen", "event-" + sha(event_id)))


def prune():
    """Remove remembered nonces/event ids older than a day (called now and then)."""
    folder = os.path.join(DATA, "seen")
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < time.time() - 86400:
                os.unlink(path)
        except OSError:
            pass


def apply_if_newer(order):
    """Keep the order only if its sequence is higher than the one already stored. Returns True when it was applied."""
    path = os.path.join(DATA, "orders", "%d.json" % int(order["id"]))
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None
    fresh = old is None or int(order["sequence"]) > int(old["sequence"])
    if fresh:
        tmp = "%s.tmp%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(order, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return fresh


class Handler(BaseHTTPRequestHandler):
    timeout = 15  # a client that stalls cannot hold a thread for ever

    def _reply(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(404 if self.path.split("?")[0] != HOOK_PATH else 405, {"ok": False, "error": "post_only"})

    do_PUT = do_DELETE = do_PATCH = do_GET

    def do_POST(self):
        if self.path.split("?")[0] != HOOK_PATH:  # 1.
            return self._reply(404, {"ok": False, "error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._reply(411, {"ok": False, "error": "length_required"})
        if length < 0 or length > MAX_BODY:
            return self._reply(413, {"ok": False, "error": "too_large"})
        raw = self.rfile.read(length)
        try:
            event = ResellingKoriClient.verify_webhook(KEY_ID, SECRET, dict(self.headers.items()), raw)  # 2. key, time, signature
        except ResellingKoriError as e:
            sys.stderr.write("ResellingKori webhook rejected: %s\n" % e)
            return self._reply(e.http_status or 401, {"ok": False})
        if event["type"] == "callback.verify":  # 3. the registration challenge
            sys.stderr.write("ResellingKori webhook: registration challenge answered.\n")
            return self._reply(200, ResellingKoriClient.challenge_proof(SECRET, event))
        try:
            with LOCK:
                if random.random() < 0.01:
                    prune()
                nonce = self.headers.get("X-RK-Nonce", "")  # 4. repeats
                if not first_time("nonce", nonce) or already_done(event["id"]):
                    return self._reply(200, {"ok": True, "duplicate": True})
                # Every event has data.order. An event type you do not know yet still means "this order changed": use data.order.
                order = (event.get("data") or {}).get("order")
                if not isinstance(order, dict):
                    return self._reply(200, {"ok": True, "ignored": "no_order"})
                applied = apply_if_newer(order)  # 5. never go backwards
                if applied:
                    with open(os.path.join(DATA, "webhook-events.ndjson"), "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event_id": event["id"], "type": event["type"],
                            "sequence": order["sequence"], "reference": order["external_order_id"], "status": order["status"], "order": order,
                        }, ensure_ascii=False) + "\n")
                first_time("event", event["id"])  # Marked only after the event was stored: a crash above means a retry is processed.
        except Exception as e:  # noqa: BLE001 - whatever went wrong, ResellingKori must be told to retry later
            sys.stderr.write("ResellingKori webhook: could not store the event: %s\n" % e)
            return self._reply(500, {"ok": False, "error": "storage_failed"})  # 5xx: ResellingKori will send it again later.
        sys.stderr.write('ResellingKori webhook: %s order %s (#%s) is now "%s" (sequence %s)%s\n' % (
            event["type"], order["external_order_id"], order["number"], order["status"], order["sequence"], "" if applied else " - old news, ignored"))
        return self._reply(200, {"ok": True, "applied": applied})  # 6. fast answer


def main():
    if not KEY_ID or not SECRET:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    for sub in ("", "seen", "orders"):
        os.makedirs(os.path.join(DATA, sub), mode=0o700, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    sys.stderr.write("Listening on http://%s:%d%s - register the PUBLIC https address of this endpoint with 06_register_webhook.py\n" % (HOST, PORT, HOOK_PATH))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
