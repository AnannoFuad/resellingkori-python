#!/usr/bin/env python3
"""
Example 4 - follow your orders by asking ResellingKori (polling). Use this when you cannot receive webhooks,
or as a slow safety net next to them (every few hours).

    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 04_follow_orders.py [--loop=600] [--full] [--dir=./rk-data]

Without --loop it asks once and stops - run it from cron every 5 to 15 minutes.
With --loop=SECONDS it keeps running and asks again after that many seconds.

What it does
  1. asks for the orders that changed since the previous poll (the first poll asks for all of them),
  2. applies a change only when its "sequence" is higher than the one it already holds (never go backwards),
  3. prints every change, and lists orders that were deleted on ResellingKori's side ("gone"),
  4. only then saves <dir>/orders.state.json { "cursor": <server_time>, "last_full": ..., "orders": { "<your reference>": {...} } }.

Once a day (and with --full) it asks for ALL orders instead of only the changed ones. Only that full poll can
tell you about an order that was deleted on ResellingKori's side: a deleted order does not "change", it is just gone.

The cursor is the "server_time" that ResellingKori reported at the START of the poll, so an order that changes
while you are still reading the pages is simply received again next time.

In your own system replace the part marked APPLY by an update of YOUR order (status, tracking number, e-mail).
Exit code 0 = fine, 1 = ResellingKori refused or was not reachable, 2 = configuration problem.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("orders"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"cursor": None, "orders": {}}


def save_state(path, state):
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def stamp(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC"


def poll(rk, state_file, force_full=False):
    """One poll. Returns the number of changes applied. Raises ResellingKoriError."""
    state = load_state(state_file)
    full = force_full or not state.get("cursor") or time.time() - float(state.get("last_full") or 0) > 86400
    since, nxt, changes, gone, page = (None if full else state["cursor"]), None, 0, set(), 1
    if full:
        print("Full poll: asking for every order (once a day, and with --full; only a full poll can tell you about orders deleted on ResellingKori's side).")
    while True:
        res = rk.list_orders(page=page, per_page=100, order="asc", updated_after=since)
        if nxt is None:
            nxt = int(res["server_time"])
        for o in res["orders"]:
            ref = str(o["external_order_id"])
            have = state["orders"].get(ref)
            if have and int(o["sequence"]) <= int(have["sequence"]):
                continue  # Same or older than what we already applied: ignore.
            track = ("%s %s" % (o["tracking"]["courier"], o["tracking"]["number"])).strip() if o["tracking"]["number"] else ""
            # APPLY: update YOUR order here (status, tracking number, notify your customer).
            print("[%s] %s (ResellingKori #%s): %s -> %s%s%s%s" % (
                stamp(o["updated_at"]), ref, o["number"],
                have["status"] if have else "(new to this script)", o["status"],
                (" | tracking %s%s" % (track, " " + o["tracking"]["url"] if o["tracking"]["url"] else "")) if track else "",
                " | NEEDS ATTENTION: " + o["error"] if o["error"] else "",
                " | can still be cancelled" if o["cancellable"] else "",
            ))
            state["orders"][ref] = {"id": o["id"], "status": o["status"], "sequence": int(o["sequence"]), "tracking": track}
            changes += 1
        gone.update(str(r) for r in res["gone"])
        page += 1
        if page > int(res["total_pages"]):
            break
    for ref in sorted(gone):
        print("Order %s was deleted on ResellingKori's side. Contact ResellingKori if you did not expect this." % ref)
    state["cursor"] = nxt  # Saved only now: a crash above simply repeats this poll.
    if full:
        state["last_full"] = nxt
    save_state(state_file, state)
    return changes


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # every line appears at once, also in a log file or a pipe
    key_id, secret = os.environ.get("RK_KEY_ID"), os.environ.get("RK_SECRET")
    if not key_id or not secret:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    loop = 0
    force = False
    data_dir = os.environ.get("RK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "rk-data")
    for arg in sys.argv[1:]:
        m = re.match(r"^--loop=(\d+)$", arg)
        if m and int(m.group(1)) >= 60:
            loop = int(m.group(1))
        elif arg == "--full":
            force = True
        elif arg.startswith("--dir="):
            data_dir = arg[6:]
        else:
            print("Unknown option %s. Usage: python3 04_follow_orders.py [--loop=600 (at least 60)] [--full] [--dir=./rk-data]" % arg, file=sys.stderr)
            return 2
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    state_file = os.path.join(data_dir, "orders.state.json")
    try:
        kwargs = {"base_url": os.environ["RK_BASE_URL"]} if os.environ.get("RK_BASE_URL") else {}
        rk = ResellingKoriClient(key_id, secret, **kwargs)
    except ValueError as e:
        print("Configuration problem: %s" % e, file=sys.stderr)
        return 2
    while True:
        try:
            n = poll(rk, state_file, force)
            force = False  # --full applies to the first poll; later polls decide for themselves.
            print("%s UTC: %d change(s). Asking again %s." % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), n, "in %d s" % loop if loop else "next time you run this script"))
        except ResellingKoriError as e:
            print("POLL FAILED: %s (HTTP %s) %s" % (e.api_code, e.http_status, e), file=sys.stderr)
            if not e.retryable or not loop:
                return 1
            print("  Will try again.", file=sys.stderr)
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    sys.exit(main())
