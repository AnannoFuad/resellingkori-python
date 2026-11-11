#!/usr/bin/env python3
"""
Example 1 - check the connection.

    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 01_check_connection.py

Optional: RK_BASE_URL (default https://resellingkori.com/wp-json/rk/v1).
Exit code 0 = everything is fine, 1 = ResellingKori refused or was not reachable, 2 = credentials missing.
Needs Python 3.8 or newer. No packages to install.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402

HINTS = {
    "rkapi_auth_invalid": "The signature does not match: a wrong secret or key id, or what you sign is not what you send (route, query, body).",
    "rkapi_auth_missing": "The X-RK-* headers did not arrive. A proxy, firewall or hosting layer may be removing them.",
    "rkapi_auth_expired": "Your server clock is off by more than five minutes. Fix the time (NTP) on your server.",
    "rkapi_key_revoked": "This key was revoked or rotated. Ask ResellingKori for the new key.",
    "rkapi_store_disabled": "Your store is switched off at ResellingKori. Contact ResellingKori.",
    "rkapi_ip_denied": "Your key only works from the server addresses registered with ResellingKori.",
    "rkapi_locked": "Too many failed sign-ins from this address. Wait a few minutes, then fix the credentials.",
    "network": "ResellingKori could not be reached. Check the internet connection, firewall and RK_BASE_URL.",
}


def main():
    key_id = os.environ.get("RK_KEY_ID")
    secret = os.environ.get("RK_SECRET")
    if not key_id or not secret:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    kwargs = {"base_url": os.environ["RK_BASE_URL"]} if os.environ.get("RK_BASE_URL") else {}
    try:
        rk = ResellingKoriClient(key_id, secret, **kwargs)
        me = rk.ping()
    except ValueError as e:
        print("Configuration problem: %s" % e, file=sys.stderr)
        return 2
    except ResellingKoriError as e:
        print("NOT CONNECTED: %s (HTTP %s) %s" % (e.api_code, e.http_status, e), file=sys.stderr)
        if e.api_code in HINTS:
            print("  What to do: " + HINTS[e.api_code], file=sys.stderr)
        if e.api_code == "rkapi_ip_denied" and e.data.get("ip"):
            print("  ResellingKori saw your server as " + e.data["ip"], file=sys.stderr)
        return 1

    store, agent, cb, lim = me["store"], me["agent"], me["callback"], me["limits"]
    print("Connected.")
    print("  Store        : %s (platform: %s)" % (store["name"], store["platform"]))
    print("  Key          : %s (%s)" % (store["key_id"], store["key_status"]))
    print("  Permissions  : %s" % ", ".join(store["scopes"]))
    print("  IP-restricted: %s" % ("yes" if store["ip_restricted"] else "no"))
    print("  Sales agent  : %s (%s)" % (agent["name"], "valid" if agent["valid"] else "NOT VALID - orders will be refused, contact ResellingKori"))
    print("  Webhook      : %s%s" % (cb["state"], " - " + cb["url"] if cb["url"] else ""))
    print("  Clock        : your server differs from ResellingKori by %d s (the client corrects this automatically)" % (me["server_time"] - int(time.time())))
    print("  Limits       : %d requests/minute, up to %d order lines, order total up to %s, cash to collect up to %s" % (lim["requests_per_minute"], lim["max_lines"], lim["max_order_amount"], lim["max_cod_amount"]))
    missing = [s for s in ("catalog:read", "orders:read", "orders:write") if s not in store["scopes"]]
    if missing:
        print("  Note: this key cannot use: %s. Ask ResellingKori if your integration needs them." % ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
