"""
ResellingKori Partner API - Python client (single file, standard library only, Python 3.8+).

    from resellingkori_client import ResellingKoriClient
    rk = ResellingKoriClient(os.environ["RK_KEY_ID"], os.environ["RK_SECRET"])
    me = rk.ping()
    page = rk.list_products(page=1, per_page=50)
    check = rk.validate_order(order)   # nothing is created
    res = rk.create_order(order)       # created once, even if the network fails half-way

Server-side only. Never ship the secret to a browser or a mobile app.
License: GPL-2.0-or-later. Version 1.1.1.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.1.1"
WEBHOOK_ROUTE = "/rkc/v1/webhook"


class ResellingKoriError(Exception):
    """Every error from the API or the network. Branch on api_code and retryable."""

    def __init__(self, message, api_code, http_status, data=None):
        super().__init__(message)
        self.api_code = api_code
        self.http_status = http_status
        self.data = data or {}
        self.retryable = (
            http_status == 0
            or http_status >= 500
            or http_status in (408, 425, 429)
            or api_code in ("rkapi_in_progress", "rkapi_busy")
        )


def _enc(v):
    return urllib.parse.quote(str(v), safe="-_.~")


def build_query(params):
    items = []
    for k in sorted(k for k, v in (params or {}).items() if v is not None):
        v = params[k]
        if isinstance(v, (dict, list, tuple, set)):
            raise TypeError("Query parameters must be scalars.")
        if isinstance(v, bool):
            v = "1" if v else "0"
        items.append(_enc(k) + "=" + _enc(v))
    return "&".join(items)


def sign(secret, timestamp, nonce, method, route, query, body=b""):
    if isinstance(body, str):
        body = body.encode("utf-8")
    canonical = "\n".join(["RK2", str(int(timestamp)), nonce, method.upper(), route, query, hashlib.sha256(body).hexdigest()])
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never follow redirects
        return None


class ResellingKoriClient:
    def __init__(self, key_id, secret, base_url="https://resellingkori.com/wp-json/rk/v1", timeout=30, transport=None):
        import re

        if not re.match(r"^rkk_[a-f0-9]{8,64}$", str(key_id)) or not secret:
            raise ValueError("A ResellingKori key id (rkk_...) and secret are required.")
        self.key_id = str(key_id)
        self.secret = str(secret)
        self.timeout = timeout
        self.clock_offset = 0
        self._transport = transport  # tests: fn(method, url, headers, body) -> (status, text)
        self._opener = urllib.request.build_opener(_NoRedirect)
        u = urllib.parse.urlsplit(base_url)
        qs = urllib.parse.parse_qs(u.query)
        origin = u.scheme + "://" + u.netloc
        if "rest_route" in qs:
            self.plain = True
            self.site = origin + u.path.rstrip("/")
            self.prefix = "/" + qs["rest_route"][0].strip("/")
        else:
            at = u.path.find("/wp-json/")
            if at < 0:
                raise ValueError("base_url must contain /wp-json/ (or ?rest_route=).")
            self.plain = False
            self.site = origin + u.path[: at + 8]
            self.prefix = "/" + u.path[at + 8 :].strip("/")

    # ------------------------------------------------------------------ transport
    def _send(self, method, url, headers, body):
        if self._transport:
            return self._transport(method, url, headers, body)
        req = urllib.request.Request(url, data=None if method == "GET" else body, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # network, TLS, timeout
            return 0, str(e)

    def request(self, method, path, query=None, body=None):
        method = method.upper()
        route = self.prefix + "/" + str(path).lstrip("/")
        q = build_query(query or {})
        raw = b"" if body is None else (body.encode("utf-8") if isinstance(body, str) else body)
        for attempt in range(2):
            ts = int(time.time()) + self.clock_offset
            nonce = os.urandom(16).hex()
            headers = {
                "Accept": "application/json",
                "User-Agent": "ResellingKori-Python-Client/" + VERSION,
                "X-RK-Key": self.key_id,
                "X-RK-Timestamp": str(ts),
                "X-RK-Nonce": nonce,
                "X-RK-Signature": sign(self.secret, ts, nonce, method, route, q, raw),
            }
            if method != "GET":
                headers["Content-Type"] = "application/json"
            if self.plain:
                url = self.site + "/?rest_route=" + _enc(route) + ("&" + q if q else "")
            else:
                url = self.site + route + ("?" + q if q else "")
            status, text = self._send(method, url, headers, raw)
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if 200 <= status < 300 and isinstance(data, dict):
                return data
            code = data.get("code") if isinstance(data, dict) and data.get("code") else ("network" if status == 0 else "http_%d" % status)
            info = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
            if attempt == 0 and code == "rkapi_auth_expired" and info.get("server_time"):
                self.clock_offset = int(info["server_time"]) - int(time.time())
                continue
            msg = data.get("message") if isinstance(data, dict) and data.get("message") else ("Network error: " + text if status == 0 else "Unexpected HTTP %d answer." % status)
            raise ResellingKoriError(msg, code, status, info)
        raise ResellingKoriError("Clock could not be synchronised.", "rkapi_auth_expired", 401)

    @staticmethod
    def _json(data):
        return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    # ------------------------------------------------------------------ endpoints
    def ping(self):
        return self.request("GET", "/ping")

    def list_products(self, **params):
        """page, per_page (<=100), updated_after (unix|ISO), category, in_stock, sku, include"""
        return self.request("GET", "/products", params)

    def each_product(self, updated_after=None, per_page=100):
        page = 1
        while True:
            res = self.list_products(page=page, per_page=per_page, updated_after=updated_after)
            for item in res["items"]:
                yield item
            page += 1
            if page > int(res["total_pages"]):
                break

    def categories(self, hide_empty=False):
        return self.request("GET", "/categories", {"hide_empty": 1} if hide_empty else {})

    def product(self, product_id, include=""):
        return self.request("GET", "/products/%d" % int(product_id), {"include": include} if include else {})

    def products_by_sku(self, skus, include=""):
        q = {"skus": ",".join(str(s) for s in list(skus)[:50])}
        if include:
            q["include"] = include
        return self.request("GET", "/products/by-sku", q)

    def search_products(self, q, per_page=20):
        return self.request("GET", "/products/search", {"q": q, "per_page": per_page})

    def validate_order(self, order):
        """Dry run: same checks as create_order(), nothing created or reserved. "schema" and "currency" default to 2 and BDT, as in create_order()."""
        order = dict({"schema": 2, "currency": "BDT"}, **order)
        return self.request("POST", "/orders/validate", None, self._json(order))

    def create_order(self, order, attempts=4):
        """Create an order exactly once; after a lost answer: look up first, resend the identical body only if absent."""
        order = dict({"schema": 2, "currency": "BDT"}, **order)
        if not order.get("external_order_id"):
            raise ValueError("external_order_id is required (your own order reference).")
        body = self._json(order)
        ext = str(order["external_order_id"])
        last = None
        for i in range(max(1, attempts)):
            if i > 0:
                time.sleep(min(8, 2 ** i))
                found = self._recover(ext)
                if isinstance(found, dict):
                    return found
                if found == "creating":
                    continue
            try:
                return self.request("POST", "/orders", None, body)
            except ResellingKoriError as e:
                if not e.retryable:
                    raise
                last = e
        raise last or ResellingKoriError("Order could not be confirmed.", "network", 0)

    def _recover(self, ext):
        try:
            res = self.lookup_order(ext)
        except ResellingKoriError as e:
            if e.api_code == "rkapi_order_not_found":
                return None
            return "creating" if e.retryable else None
        if not res.get("order"):
            return "creating"
        return {"order": res["order"], "duplicate": True, "recovered": True}

    def lookup_order(self, external_order_id):
        return self.request("GET", "/orders/lookup", {"external_order_id": str(external_order_id)})

    def order(self, order_id):
        return self.request("GET", "/orders/%d" % int(order_id))

    def list_orders(self, **params):
        """page, per_page, status, updated_after, order (asc|desc)"""
        return self.request("GET", "/orders", params)

    def order_statuses(self, ids):
        return self.request("GET", "/orders/status", {"ids": ",".join(str(int(i)) for i in list(ids)[:100])})

    def cancel_order(self, external_order_id, reason=""):
        return self.request("POST", "/orders/cancel", None, self._json({"external_order_id": str(external_order_id), "reason": reason}))

    def register_webhook(self, url):
        return self.request("POST", "/connection", None, self._json({"callback_url": url}))

    def remove_webhook(self):
        return self.request("POST", "/connection", None, self._json({"remove_callback": True}))

    # ------------------------------------------------------------------ webhooks
    @staticmethod
    def verify_webhook(key_id, secret, headers, raw_body, tolerance=300):
        """Verify a delivery and return the event (raises ResellingKoriError 401/400).
        You must still drop repeated nonces / event ids and events with a lower sequence than already applied."""
        import re

        h = {str(k).lower(): str(v[0] if isinstance(v, (list, tuple)) else v).strip() for k, v in (headers or {}).items()}
        raw = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode("utf-8")
        ts, nonce, sig = h.get("x-rk-timestamp", ""), h.get("x-rk-nonce", ""), h.get("x-rk-signature", "").lower()
        bad = ResellingKoriError("Invalid webhook signature.", "invalid_signature", 401)
        if not hmac.compare_digest(h.get("x-rk-key", ""), str(key_id)) or not re.match(r"^\d{9,12}$", ts) \
                or not re.match(r"^[A-Za-z0-9_\-]{8,64}$", nonce) or not re.match(r"^[a-f0-9]{64}$", sig):
            raise bad
        if abs(int(time.time()) - int(ts)) > tolerance:
            raise bad
        if not hmac.compare_digest(sign(secret, ts, nonce, "POST", WEBHOOK_ROUTE, "", raw), sig):
            raise bad
        try:
            event = json.loads(raw.decode("utf-8"))
        except ValueError:
            event = None
        if not isinstance(event, dict) or not event.get("id") or not event.get("type") or str(event["id"]) != h.get("x-rk-event-id", ""):
            raise ResellingKoriError("Malformed webhook body.", "invalid_body", 400)
        return event

    @staticmethod
    def challenge_proof(secret, event):
        """Answer to a callback.verify event: {"proof": ...}."""
        return {"proof": hmac.new(secret.encode("utf-8"), ("RK2-CALLBACK\n" + str(event["challenge"])).encode("utf-8"), hashlib.sha256).hexdigest()}
