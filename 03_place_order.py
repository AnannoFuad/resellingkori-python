#!/usr/bin/env python3
"""
Example 3 - check an order, place it exactly once, cancel it.

  Check only (safe: nothing is created, nothing is reserved):
    RK_KEY_ID=rkk_... RK_SECRET=rks_... python3 03_place_order.py [--sku=AW-Y526] [--qty=1] [--price=830]

  Really place the order (ResellingKori starts processing it and a courier will be sent):
    python3 03_place_order.py --sku=AW-Y526 --qty=1 --price=830 --ref=WEB-10058 --place

  Run the SAME command again: ResellingKori answers "duplicate" and creates nothing new. This is what makes it safe
  to retry after a time-out. Your own order number is the reference; never invent a new one for a retry.

  Cancel (possible until ResellingKori has processed the order):
    python3 03_place_order.py --ref=WEB-10058 --cancel

Options  --sku       SKU of the product (or of one variation). Default: the first product that can be ordered now.
         --qty       quantity, default 1
         --price     YOUR selling price per unit in BDT, never below the base price. Default: base price + 15 %.
         --delivery  delivery charge you take from your customer, default 100
         --prepaid   what your customer has already paid you, default 0 (the courier collects the rest)
         --ref       your reference for this order (required with --place and --cancel)

The customer below is sample data. In your system it comes from your own order.
Exit code 0 = fine, 1 = ResellingKori refused or was not reachable, 2 = wrong usage or configuration.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients", "python"))
from resellingkori_client import ResellingKoriClient, ResellingKoriError  # noqa: E402

CUSTOMER = {
    "name": "Rahim Uddin",
    "phone": "01700000000",
    "email": "rahim@example.com",
    "address_1": "House 12, Road 3, Dhanmondi",
    "address_2": "",
    "city": "Dhaka",
    "state": "Dhaka",
    "postcode": "1209",
    "country": "BD",
}


# ---------------------------------------------------- money: exact, in paisa (integers), never floats

def to_paisa(amount):
    """'1190.00' or '1190' or '1190.5' -> 119000 paisa."""
    m = re.match(r"^(\d+)(?:\.(\d{1,2}))?$", str(amount).strip())
    if not m:
        raise ValueError("'%s' is not an amount (use digits and at most two decimals, for example 1190 or 1190.50)." % amount)
    return int(m.group(1)) * 100 + int((m.group(2) or "").ljust(2, "0"))


def money(paisa):
    """119000 -> '1190.00' (the format the API expects)."""
    return "%d.%02d" % (paisa // 100, paisa % 100)


def explain(e):
    """Say what went wrong in words a person can act on."""
    print("REFUSED: %s (HTTP %s) %s" % (e.api_code, e.http_status, e), file=sys.stderr)
    d = e.data or {}
    for f in d.get("fields", []):
        print("  - %s: %s%s" % (f.get("field", "?"), f.get("message", ""), " [%s]" % f["code"] if f.get("code") else ""), file=sys.stderr)
    for c in d.get("changes", []):
        print("  - line %s: you expected a base price of %s but it is now %s. Refresh your catalogue, show your customer the new price and send again." % (c["external_item_id"], c["expected_base_unit_price"], c["current_base_unit_price"]), file=sys.stderr)
    for s in d.get("shortages", []):
        print("  - %s: you asked for %s, only %s available." % (s["label"], s["requested"], s["available"]), file=sys.stderr)
    if e.retryable:
        print("  This kind of answer can be retried with the SAME order and the SAME reference.", file=sys.stderr)


def main():
    opt = {"qty": "1", "delivery": "100", "prepaid": "0"}
    act = "check"
    for arg in sys.argv[1:]:
        m = re.match(r"^--(sku|qty|price|delivery|prepaid|ref)=(.*)$", arg, re.S)
        if arg == "--place":
            act = "place"
        elif arg == "--cancel":
            act = "cancel"
        elif m:
            opt[m.group(1)] = m.group(2)
        else:
            print("Unknown option %s. Read the top of this file for the usage." % arg, file=sys.stderr)
            return 2
    key_id, secret = os.environ.get("RK_KEY_ID"), os.environ.get("RK_SECRET")
    if not key_id or not secret:
        print("Set RK_KEY_ID and RK_SECRET first (see START-HERE.md).", file=sys.stderr)
        return 2
    if act != "check" and not opt.get("ref"):
        print("--ref is required: it is YOUR reference for the order, for example your own order number (WEB-10058).\nUse the same reference whenever you repeat or cancel this order.", file=sys.stderr)
        return 2
    ref = opt.get("ref") or "EXAMPLE-CHECK"

    try:
        kwargs = {"base_url": os.environ["RK_BASE_URL"]} if os.environ.get("RK_BASE_URL") else {}
        rk = ResellingKoriClient(key_id, secret, **kwargs)

        # ------------------------------------------------------------------------------------------- cancel
        if act == "cancel":
            res = rk.cancel_order(ref, "Cancelled from the example script")
            print("Order %s was already cancelled." % ref if res.get("already_cancelled") else "Order %s is cancelled. The stock returns to ResellingKori." % ref)
            return 0

        # ----------------------------------------------------------- 1. the product, with live price and stock
        if not opt.get("sku"):
            pick = next((p for p in rk.list_products(in_stock=1, per_page=50)["items"]
                         if p["type"] == "simple" and p["purchasable"] and p["stock_status"] == "instock"), None)
            if not pick:
                print("ResellingKori shows no simple product that can be ordered right now. Try again later or pass --sku=.", file=sys.stderr)
                return 1
            opt["sku"] = pick["sku"]
            print("No --sku given: using the first product that can be ordered now (%s)." % pick["sku"])
        found = rk.products_by_sku([opt["sku"]])
        if not found.get("matches"):
            print("ResellingKori has no product with SKU %s." % opt["sku"], file=sys.stderr)
            return 1
        p = next(iter(found["matches"].values()))
        variation = int(p.get("matched_variation_id") or 0)
        base, label, unit_sku = p["base_price"], p["name"], p["sku"]
        if p["type"] == "variable" and not variation:
            print("%s is a product with variations. Order one variation: pass the SKU of that variation." % opt["sku"], file=sys.stderr)
            return 2
        if variation:  # A variation has its own price, stock and SKU.
            for v in p["variations"]:
                if int(v["id"]) == variation:
                    base, label, unit_sku = v["base_price"], label + " - " + v["label"], v["sku"]
        print("Product : %s (SKU %s)\nBase price (what you pay ResellingKori): %s BDT, stock: %s" % (label, unit_sku, base, p["stock_status"] if p["stock_quantity"] is None else p["stock_quantity"]))

        # --------------------------------------------------------------------------------- 2. build the order
        qty = int(opt["qty"])
        base_paisa = to_paisa(base)
        price_paisa = to_paisa(opt["price"]) if opt.get("price") is not None else (base_paisa * 115 + 9999) // 10000 * 100  # base + 15 %, rounded up to a whole taka
        deliv_paisa = to_paisa(opt["delivery"])
        prepaid = to_paisa(opt["prepaid"])
        total_paisa = price_paisa * qty + deliv_paisa  # order_total = items + delivery
        cod_paisa = total_paisa - prepaid  # the courier collects the rest
        if qty < 1 or prepaid > total_paisa:
            print("Quantity must be at least 1 and --prepaid cannot be more than the order total (%s)." % money(total_paisa), file=sys.stderr)
            return 2
        order = {
            "schema": 2,
            "external_order_id": ref,
            "external_order_number": "#" + ref,
            "currency": "BDT",
            "customer": CUSTOMER,
            "items": [{
                "external_item_id": 1,
                "rk_product_id": int(p["id"]),
                "rk_variation_id": variation,
                "sku": unit_sku,
                "name": label,
                "quantity": qty,
                "selling_unit_price": money(price_paisa),
                "expected_base_unit_price": base,  # the price guard: refuse if ResellingKori's price has changed meanwhile
            }],
            "payment": {
                "method": "prepaid" if cod_paisa == 0 else "cod",
                "delivery_charge": money(deliv_paisa),
                "prepaid_amount": money(prepaid),
                "cod_amount": money(cod_paisa),
            },
            "partial": False,
            "note": "Example order from the ResellingKori partner developer kit.",
        }

        # ------------------------------------------------------------------------- 3. check it (always)
        check = rk.validate_order(order)
        f = check["financials"]
        print("\nChecked by ResellingKori: %s" % ("the order would be accepted now" if check["valid"] else "NOT valid"))
        print("  You sell %d x %s = %s | delivery %s | order total %s | already paid %s | cash the courier collects %s" % (qty, money(price_paisa), f["selling_total"], f["delivery_charge"], f["order_total"], f["prepaid_amount"], f["cod_amount"]))
        print("  Cost of the goods to you %s | YOUR PROFIT %s BDT" % (f["base_total"], f["profit_total"]))
        if check.get("existing_order"):
            ex = check["existing_order"]
            if ex.get("same_content"):
                print("  Note: the reference %s was already used with exactly this content (ResellingKori order #%s). Sending it again creates nothing new." % (ref, ex["order_id"]))
            else:
                print("  WARNING: the reference %s was already used for DIFFERENT content (ResellingKori order #%s). ResellingKori would refuse this order (rkapi_order_conflict) - use a new reference." % (ref, ex["order_id"]))
        if act != "place":
            print("\nThis was only a check - nothing was created. To place the order add: --ref=YOUR-OWN-ORDER-NUMBER --place")
            return 0

        # ------------------------------------------------------------------------- 4. place it, exactly once
        res = rk.create_order(order)
        o = res["order"]
        if res.get("duplicate"):
            print("\n%s - nothing new was created." % ("The answer to the first attempt was lost, but the order exists" if res.get("recovered") else "ResellingKori already had exactly this order"))
        else:
            print("\nORDER CREATED. Stock is reserved and ResellingKori is processing it.")
        print("  ResellingKori order #%s | your reference %s | status %s | cancellable: %s" % (o["number"], o["external_order_id"], o["status"], "yes" if o["cancellable"] else "no"))
        print("  Follow it with 04_follow_orders.py or with a webhook (05/06). To cancel: python3 03_place_order.py --ref=%s --cancel" % ref)
        return 0
    except ResellingKoriError as e:
        explain(e)
        return 1
    except ValueError as e:
        print("Problem with your input: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
