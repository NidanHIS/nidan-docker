#!/usr/bin/env python3
"""Paywall test data on the full harness Odoo (the paywall tests KC-14-04/05/07 skip without it). Idempotent.

  python3 -I tests/e2e-auth/harness/paywall-fixture.py      prints PAYWALL_PATIENT=<uuid>

The patient is the harness OpenMRS patient "Harness Paywall" (OpenMRS refuses a paywall check for a patient it does not
know). Creates in Odoo, once: that patient (res.partner with patient_uuid), a Rs 500 service product and one UNPAID
(draft) POS order in an open session of "Harness Shop A". The paywall then answers blocked with cart_amount 500.
Uses Odoo's internal JSON-RPC (127.0.0.1:18069) as the harness admin. Harness only: never on a real site.
"""
import json
import os
import subprocess
import sys
import urllib.request

ODOO = "http://127.0.0.1:18069/jsonrpc"
DB = "nidan"
ADMIN_PW = os.environ.get("ODOO_ADMIN_PASSWORD", "Harness-Odoo-Admin-0123456789")
PATIENT_UUID = "7a516e35-8dd4-4a38-b961-6a5160406ab9"     # OpenMRS "Harness Paywall"; fixed, so PAYWALL_PATIENT never changes
SHOP = "Harness Shop A"


def rpc(model, method, args, kwargs=None):
    body = {"jsonrpc": "2.0", "method": "call", "id": 1, "params": {
        "service": "object", "method": "execute_kw", "args": [DB, 2, ADMIN_PW, model, method, args, kwargs or {}]}}
    req = urllib.request.Request(ODOO, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if out.get("error"):
        raise SystemExit(f"{model}.{method}: {json.dumps(out['error'])[:500]}")
    return out["result"]


def one(model, domain, values):
    found = rpc(model, "search", [domain], {"limit": 1})
    return found[0] if found else rpc(model, "create", [values])


def in_openmrs():
    out = subprocess.run(["docker", "exec", "-i", "nidan-sso-openmrs-harness-openmrs-db-1", "sh", "-c",
                          'exec mysql -uopenmrs -p"$MYSQL_PASSWORD" openmrs -N'], capture_output=True, text=True,
                         input=f"SELECT COUNT(*) FROM person WHERE uuid = '{PATIENT_UUID}' AND voided = 0;\n")
    return out.stdout.strip() == "1"


def main():
    if not in_openmrs():
        raise SystemExit(f"the OpenMRS patient {PATIENT_UUID} (Harness Paywall) is missing: create it in OpenMRS first")
    partner = one("res.partner", [("patient_uuid", "=", PATIENT_UUID)],
                  {"name": "Harness Paywall Patient", "patient_uuid": PATIENT_UUID})
    product = one("product.product", [("default_code", "=", "HARNESS-PAYWALL-500")],
                  {"name": "Paywall test item", "default_code": "HARNESS-PAYWALL-500", "type": "service",
                   "list_price": 500.0, "available_in_pos": True, "taxes_id": [(6, 0, [])]})
    if rpc("pos.order", "search_count", [[("partner_id", "=", partner), ("state", "=", "draft")]]) == 0:
        config = rpc("pos.config", "search", [[("name", "=", SHOP)]], {"limit": 1})
        if not config:
            raise SystemExit(f"no POS config {SHOP!r} on the harness Odoo (see README.md 'Full harness')")
        sessions = rpc("pos.session", "search", [[("config_id", "=", config[0]), ("state", "!=", "closed")]], {"limit": 1})
        session = sessions[0] if sessions else rpc("pos.session", "create", [{"config_id": config[0]}])
        rpc("pos.order", "create", [{
            "session_id": session, "partner_id": partner, "amount_tax": 0.0, "amount_total": 500.0,
            "amount_paid": 0.0, "amount_return": 0.0,
            "lines": [(0, 0, {"product_id": product, "qty": 1, "price_unit": 500.0,
                              "price_subtotal": 500.0, "price_subtotal_incl": 500.0})]}])
    print(f"PAYWALL_PATIENT={PATIENT_UUID}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
