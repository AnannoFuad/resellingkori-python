#!/usr/bin/env python3
"""
Example 2 - copy the ResellingKori catalogue to your side and keep it up to date.

    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 02_sync_catalogue.py [--full] [--dir=./rk-data]

First run (or --full): every product is downloaded. Later runs: only the products that changed since the newest
"date_modified" you already have. Run with --full about once a day: an incremental run cannot tell you that a
product has disappeared from ResellingKori's catalogue, a full run can (it lists them as "removed").

Writes   <dir>/catalogue.json        { "<product id>": Product, ... }
         <dir>/catalogue.state.json  { "cursor": <unix seconds>, "last_full": <unix seconds> }

This example keeps the data in a JSON file so that it runs anywhere. In your own system replace the two "SAVE"
places by writes to your database (key = the product "id"; update it when it is received again - that is normal).

Exit code 0 = synchronised, 1 = ResellingKori refused or was not reachable, 2 = configuration problem.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    """Write a JSON file so that a crash can never leave half a file behind."""
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def to_unix(iso):
    """ISO 8601 ('2026-11-02T10:15:30+00:00') -> unix seconds."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def main():
    key_id, secret = os.environ.get("RK_KEY_ID"), os.environ.get("RK_SECRET")
    if not key_id or not secret:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    full = False
    data_dir = os.environ.get("RK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "rk-data")
    for arg in sys.argv[1:]:
        if arg == "--full":
            full = True
        elif arg.startswith("--dir="):
            data_dir = arg[6:]
        else:
            print("Unknown option %s. Usage: python3 02_sync_catalogue.py [--full] [--dir=./rk-data]" % arg, file=sys.stderr)
            return 2
    os.makedirs(data_dir, mode=0o700, exist_ok=True)

    catalogue_file = os.path.join(data_dir, "catalogue.json")
    state_file = os.path.join(data_dir, "catalogue.state.json")
    before = load_json(catalogue_file)
    catalogue = {} if full else dict(before)
    state = load_json(state_file)
    cursor = int(state["cursor"]) if (not full and catalogue and state.get("cursor")) else None

    try:
        kwargs = {"base_url": os.environ["RK_BASE_URL"]} if os.environ.get("RK_BASE_URL") else {}
        rk = ResellingKoriClient(key_id, secret, **kwargs)
    except ValueError as e:
        print("Configuration problem: %s" % e, file=sys.stderr)
        return 2
    if cursor is None:
        print("Full download of the catalogue...")
    else:
        print("Incremental sync: products changed since %s UTC..." % datetime.fromtimestamp(cursor, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    seen, added, updated, newest = set(), 0, 0, cursor or 0
    try:
        for product in rk.each_product(cursor, 100):
            pid = str(product["id"])
            if pid in catalogue:
                updated += 1
            else:
                added += 1
            catalogue[pid] = product  # SAVE (1): insert or update by id.
            seen.add(pid)
            newest = max(newest, to_unix(product["date_modified"]))
    except ResellingKoriError as e:
        print("STOPPED: %s (HTTP %s) %s" % (e.api_code, e.http_status, e), file=sys.stderr)
        if e.api_code == "rkapi_scope_denied":
            print("  Your key has no catalog:read permission. Ask ResellingKori.", file=sys.stderr)
        print("  Nothing was saved; the next run starts from the same point.", file=sys.stderr)
        return 1

    # A full download is the complete truth: whatever was stored before and was not received now is gone.
    removed = []
    if cursor is None:
        removed = [pid for pid in before if pid not in seen]
        for pid in removed:
            catalogue.pop(pid, None)  # SAVE (2): delete or hide it in your shop.

    save_json(catalogue_file, catalogue)
    save_json(state_file, {
        "cursor": newest,  # The newest change received; the next run asks "since then".
        "last_full": int(time.time()) if cursor is None else int(state.get("last_full") or 0),
    })

    products = list(catalogue.values())
    orderable = sum(1 for p in products if p["stock_status"] != "outofstock" and p["purchasable"])
    variable = sum(1 for p in products if p["type"] == "variable")
    print("Done. Received %d product(s): %d new, %d changed or unchanged-but-resent." % (len(seen), added, updated))
    if cursor is None:
        more = ", ..." if len(removed) > 10 else ""
        print("      No longer in ResellingKori's catalogue: %d%s" % (len(removed), " (ids " + ", ".join(removed[:10]) + more + ")" if removed else ""))
    print("Your copy now holds %d product(s): %d can be ordered now, %d have variations (order those with rk_variation_id)." % (len(products), orderable, variable))
    print("Saved in %s" % data_dir)
    for p in products[:5]:
        print("  #%-7s %-14s base price %10s  %-9s %s" % (p["id"], p["sku"], p["base_price"], p["stock_status"], p["name"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
