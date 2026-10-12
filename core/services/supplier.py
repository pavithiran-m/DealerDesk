import re
import time
from decimal import Decimal
import requests
from django.conf import settings
from django.db import transaction
from core.models import Product

MONEY_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


class SupplierSyncError(Exception):
    """Raised when supplier sync fails permanently."""
    pass


def sync_supplier_catalogue() -> dict:
    """Executes a full supplier catalogue sync per Section 8 of the API Contract.
    
    Returns:
        dict with keys: created, updated, unchanged, rejected, pages_fetched, requests_made
    Raises:
        SupplierSyncError if supplier fails after retries or is unavailable.
    """
    base_url = (getattr(settings, "SUPPLIER_API_URL", "") or "http://127.0.0.1:8099").rstrip("/")
    api_key = getattr(settings, "SUPPLIER_API_KEY", "")

    cursor = None
    pages_fetched = 0
    requests_made = 0
    raw_items = []

    # 1. Fetch all pages following next_cursor
    while True:
        url = f"{base_url}/v1/products?limit=20"
        if cursor:
            url += f"&cursor={cursor}"

        headers = {"X-Api-Key": api_key}
        page_success = False

        for attempt in range(1, 6):
            requests_made += 1
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_items.extend(data.get("items", []))
                    cursor = data.get("next_cursor")
                    pages_fetched += 1
                    page_success = True
                    break
                elif resp.status_code == 401:
                    # Invalid or missing key -> fail immediately
                    raise SupplierSyncError("Invalid supplier API key (401)")
                elif resp.status_code == 429:
                    retry_header = resp.headers.get("Retry-After", "1")
                    try:
                        wait_sec = float(retry_header)
                    except ValueError:
                        wait_sec = 1.0
                    time.sleep(max(0.1, wait_sec))
                    continue
                elif 500 <= resp.status_code < 600:
                    time.sleep(0.2 * (2 ** (attempt - 1)))
                    continue
                else:
                    # Unexpected client error
                    time.sleep(0.2 * (2 ** (attempt - 1)))
                    continue
            except requests.RequestException:
                time.sleep(0.2 * (2 ** (attempt - 1)))
                continue

        if not page_success:
            raise SupplierSyncError(f"Supplier request failed after 5 attempts on page with cursor {cursor}")

        if not cursor:
            break

    # 2. Filter invalid items and deduplicate by latest updated_at
    valid_items_by_sku = {}
    rejected = []

    for item in raw_items:
        sup_sku = item.get("supplier_sku")
        sku = item.get("sku")

        # Missing or empty sku
        if not sku or not isinstance(sku, str) or not sku.strip():
            rejected.append({"supplier_sku": sup_sku, "reason": "missing or empty sku"})
            continue

        sku = sku.strip()
        price_str = item.get("price")

        # Price validation: must be a positive amount with at most 2 decimals
        if not price_str or not isinstance(price_str, str):
            rejected.append({"supplier_sku": sup_sku, "reason": "invalid price"})
            continue

        price_str = price_str.strip()
        if not MONEY_REGEX.match(price_str):
            rejected.append({"supplier_sku": sup_sku, "reason": "invalid price"})
            continue

        try:
            dec_price = Decimal(price_str)
            if dec_price <= Decimal("0.00"):
                rejected.append({"supplier_sku": sup_sku, "reason": "invalid price"})
                continue
        except Exception:
            rejected.append({"supplier_sku": sup_sku, "reason": "invalid price"})
            continue

        # Duplicates across pages: keep version with latest updated_at
        curr_updated_at = item.get("updated_at", "")
        if sku in valid_items_by_sku:
            prev_item = valid_items_by_sku[sku]
            prev_updated_at = prev_item.get("updated_at", "")
            if curr_updated_at > prev_updated_at:
                valid_items_by_sku[sku] = item
        else:
            valid_items_by_sku[sku] = item

    # 3. Apply changes inside atomic transaction
    created = 0
    updated = 0
    unchanged = 0

    existing_products = {p.sku: p for p in Product.objects.filter(sku__in=valid_items_by_sku.keys())}

    with transaction.atomic():
        for sku, item in valid_items_by_sku.items():
            name = item.get("name", "").strip()
            category = item.get("category", "").strip()
            brand = item.get("brand", "").strip()
            vehicle_fitment = item.get("vehicle_fitment", "").strip()
            description = item.get("description", "").strip()
            price = Decimal(item["price"].strip())

            if sku not in existing_products:
                Product.objects.create(
                    sku=sku,
                    name=name,
                    category=category,
                    brand=brand,
                    vehicle_fitment=vehicle_fitment,
                    price=price,
                    stock=0,  # Never change our stock; new products start with 0
                    description=description,
                )
                created += 1
            else:
                prod = existing_products[sku]
                differs = (
                    prod.name != name
                    or prod.category != category
                    or prod.brand != brand
                    or prod.vehicle_fitment != vehicle_fitment
                    or prod.description != description
                    or prod.price != price
                )
                if differs:
                    prod.name = name
                    prod.category = category
                    prod.brand = brand
                    prod.vehicle_fitment = vehicle_fitment
                    prod.description = description
                    prod.price = price
                    prod.save()
                    updated += 1
                else:
                    unchanged += 1

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made,
    }
