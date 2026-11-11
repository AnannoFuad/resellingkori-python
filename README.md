# ResellingKori Python SDK

Python client and examples for the [ResellingKori Partner API](https://github.com/Resellingkori/partner-docs).

Sell ResellingKori products on your website at your own price — no `pip install` needed.

## Requirements

- Python 3.8 or higher
- No external dependencies (uses built-in `urllib` and `hmac`)

## Quick Start

```bash
git clone https://github.com/Resellingkori/resellingkori-python.git
cd resellingkori-python

export RK_KEY_ID=rkk_your_key_id
export RK_SECRET=rks_your_secret

python3 01_check_connection.py
```

Expected output: **Connected.** ✅

## Use in Your Project

Copy `resellingkori_client.py` into your project:

```python
import os
from resellingkori_client import ResellingKoriClient

client = ResellingKoriClient(
    key_id=os.environ['RK_KEY_ID'],
    secret=os.environ['RK_SECRET'],
)

# Check connection
ping = client.ping()
print(ping['store_name'])

# Browse catalogue
products = client.get_products(per_page=20)

# Validate an order (nothing created)
check = client.validate_order({
    'external_order_id': 'YOUR-ORDER-001',
    'items': [{'product_id': 42, 'quantity': 1, 'unit_price': '1200.00'}],
    'delivery_address': {
        'name':     'Customer Name',
        'phone':    '01700000000',
        'address':  '123 Example Road',
        'district': 'Dhaka',
    },
})
print(f"Profit: {check['profit']} BDT")

# Place the order
order = client.create_order({
    'external_order_id': 'YOUR-ORDER-001',
    'items': [{'product_id': 42, 'quantity': 1, 'unit_price': '1200.00'}],
    'delivery_address': {
        'name':     'Customer Name',
        'phone':    '01700000000',
        'address':  '123 Example Road',
        'district': 'Dhaka',
    },
})
print(f"Order ID: {order['id']}")
```

## Examples

| File | Run | What it does |
|---|---|---|
| `01_check_connection.py` | `python3 01_check_connection.py` | Verify credentials and permissions |
| `02_sync_catalogue.py` | `python3 02_sync_catalogue.py` | Download and sync the product catalogue |
| `03_place_order.py` | `python3 03_place_order.py --place --ref=ORDER-001` | Validate and place an order |
| `04_follow_orders.py` | `python3 04_follow_orders.py --loop=600` | Poll for order status changes |
| `05_webhook_receiver.py` | `python3 05_webhook_receiver.py` | Receive signed webhook events |
| `06_register_webhook.py` | `python3 06_register_webhook.py` | Register your webhook URL |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `RK_KEY_ID` | ✅ | — | Your Key ID (`rkk_...`) |
| `RK_SECRET` | ✅ | — | Your Secret (`rks_...`) — server only |
| `RK_BASE_URL` | — | `https://resellingkori.com/wp-json/rk/v1` | Override for sandbox |
| `RK_DATA_DIR` | — | `./rk-data` | Folder for catalogue and order data files |
| `RK_WEBHOOK_PATH` | — | `/resellingkori/webhook` | URL path for the webhook receiver |

## Security

**Never put `RK_SECRET` in browser JavaScript, a mobile app, or a public repository.**  
See [SECURITY.md](https://github.com/Resellingkori/partner-docs/blob/main/SECURITY.md) for the full guide.

## Documentation

- [Getting Started](https://github.com/Resellingkori/partner-docs/blob/main/GETTING_STARTED.md)
- [Full API Guide](https://github.com/Resellingkori/partner-docs/blob/main/PARTNER_API_GUIDE.md)
- [Sandbox Guide](https://github.com/Resellingkori/partner-docs/blob/main/SANDBOX.md)

## License

GPL-2.0-or-later
