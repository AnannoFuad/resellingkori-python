#!/usr/bin/env python3
"""
Example 6 - tell ResellingKori where to send webhooks, or switch webhooks off.

    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 06_register_webhook.py https://api.example.com/resellingkori/webhook
    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 06_register_webhook.py --remove
    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 06_register_webhook.py            (only shows the current state)

Before you register: your receiver (05_webhook_receiver.py or your own) must already be reachable at that address,
because ResellingKori sends a signed "callback.verify" challenge to it at once and your receiver must answer it.

Rules for the address: https, a public host name (not an IP address, not localhost, not a private network), port 443,
any path but no "?query", no redirects. Your key needs the permission webhooks:manage.
You may register 12 times per hour; fix the problem before you try again.

Exit code 0 = webhook verified / removed / state shown, 1 = not verified or refused, 2 = wrong usage or configuration.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402

HINTS = {
    "verified": "ResellingKori reached your receiver and it answered the challenge correctly. Status changes will now be sent to it.",
    "rejected": "The ADDRESS is not allowed (see the message). Fix the address - nothing was sent to it.",
    "unreachable": "ResellingKori could not connect (or got no answer within the time limit). Is the receiver online, on port 443, with a valid certificate?",
    "failed": 'Your receiver answered, but not with the correct proof. It must verify the signature and answer HTTP 200 with {"proof": ...} - use ResellingKoriClient.challenge_proof().',
    "removed": "The webhook is switched off. Follow your orders by polling (04_follow_orders.py).",
}


def main():
    key_id, secret = os.environ.get("RK_KEY_ID"), os.environ.get("RK_SECRET")
    if not key_id or not secret:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    args = sys.argv[1:]
    if len(args) > 1 or (args and args[0] != "--remove" and args[0].startswith("--")):
        print("Usage: python3 06_register_webhook.py [https://your-address | --remove]", file=sys.stderr)
        return 2
    try:
        kwargs = {"base_url": os.environ["RK_BASE_URL"]} if os.environ.get("RK_BASE_URL") else {}
        rk = ResellingKoriClient(key_id, secret, **kwargs)
        if not args:
            cb = rk.ping()["callback"]
            print("Webhook state: %s%s" % (cb["state"], " - " + cb["url"] if cb["url"] else ""))
            print("  (none = nothing registered, pending = registered but the challenge has not been answered, verified = working)")
            return 0
        res = rk.remove_webhook() if args[0] == "--remove" else rk.register_webhook(args[0])
    except ValueError as e:
        print("Configuration problem: %s" % e, file=sys.stderr)
        return 2
    except ResellingKoriError as e:
        print("REFUSED: %s (HTTP %s) %s" % (e.api_code, e.http_status, e), file=sys.stderr)
        if e.api_code == "rkapi_scope_denied":
            print("  Your key has no webhooks:manage permission. Ask ResellingKori to add it.", file=sys.stderr)
        elif e.api_code == "rkapi_rate_limited":
            print("  You registered too often. Wait an hour (or the time in Retry-After) and fix the problem first.", file=sys.stderr)
        return 1

    r = res.get("callback_result")
    state = r["state"] if r else "none"
    print("Result: %s" % state)
    if r and r.get("message"):
        print("  ResellingKori says: %s" % r["message"])
    if state in HINTS:
        print("  %s" % HINTS[state])
    cb = res["callback"]
    print("Registered address now: %s" % (cb["url"] + " (" + cb["state"] + ")" if cb["url"] else "(none)"))
    return 0 if state in ("verified", "removed") else 1


if __name__ == "__main__":
    sys.exit(main())
