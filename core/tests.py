from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import Product, Dealer, Order, OrderItem
from core.utils import calculate_line_item, generate_order_number


class DealerDeskCoreTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            sku="TEST-1001",
            name="Test Brake Pad",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="Bajaj Pulsar 150",
            price=Decimal("1000.00"),
            stock=10,
            description="High performance brake pad.",
        )
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-TEST-01",
            name="Apex Motors",
            city="Bengaluru",
            credit_limit=Decimal("50000.00"),
        )

    def test_health_endpoint(self):
        resp = self.client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_gst_calculation_precision(self):
        # 1459.50 x 3 = 4378.50, tax = 4378.50 * 0.18 = 788.13
        sub, tax, total = calculate_line_item(Decimal("1459.50"), 3)
        self.assertEqual(str(sub), "4378.50")
        self.assertEqual(str(tax), "788.13")
        self.assertEqual(str(total), "5166.63")

    def test_product_detail_and_404(self):
        resp = self.client.get(f"/api/products/{self.product.sku}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sku"], "TEST-1001")

        resp_404 = self.client.get("/api/products/NONEXISTENT/")
        self.assertEqual(resp_404.status_code, 404)
        self.assertEqual(resp_404.json()["error"]["code"], "NOT_FOUND")

    def test_inventory_update(self):
        resp = self.client.put(
            f"/api/inventory/{self.product.sku}/",
            {"quantity": 25, "reason": "stock count correction"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["quantity"], 25)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 25)

    def test_order_creation_and_idempotency(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": self.product.sku, "quantity": 2}],
        }
        # First request with Idempotency-Key
        resp1 = self.client.post("/api/orders/", payload, format="json", HTTP_IDEMPOTENCY_KEY="idemp-key-1")
        self.assertEqual(resp1.status_code, 201)
        data1 = resp1.json()
        self.assertEqual(data1["status"], "DRAFT")
        self.assertEqual(data1["subtotal"], "2000.00")
        self.assertEqual(data1["tax_total"], "360.00")
        self.assertEqual(data1["grand_total"], "2360.00")

        # Second request with same Idempotency-Key and same payload
        resp2 = self.client.post("/api/orders/", payload, format="json", HTTP_IDEMPOTENCY_KEY="idemp-key-1")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["order_number"], data1["order_number"])

        # Third request with same Idempotency-Key but different payload -> 409 IDEMPOTENCY_CONFLICT
        different_payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": self.product.sku, "quantity": 5}],
        }
        resp3 = self.client.post("/api/orders/", different_payload, format="json", HTTP_IDEMPOTENCY_KEY="idemp-key-1")
        self.assertEqual(resp3.status_code, 409)
        self.assertEqual(resp3.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_order_lifecycle_confirm_insufficient_stock(self):
        # Order requesting 15 units when stock is only 10
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": self.product.sku, "quantity": 15}],
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        order_num = resp.json()["order_number"]

        confirm_resp = self.client.post(f"/api/orders/{order_num}/confirm/")
        self.assertEqual(confirm_resp.status_code, 409)
        self.assertEqual(confirm_resp.json()["error"]["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(len(confirm_resp.json()["error"]["details"]), 1)

    def test_order_lifecycle_confirm_and_cancel_restores_stock(self):
        payload = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": self.product.sku, "quantity": 4}],
        }
        resp = self.client.post("/api/orders/", payload, format="json")
        order_num = resp.json()["order_number"]

        # Confirm order: stock 10 -> 6
        confirm_resp = self.client.post(f"/api/orders/{order_num}/confirm/")
        self.assertEqual(confirm_resp.status_code, 200)
        self.assertEqual(confirm_resp.json()["status"], "CONFIRMED")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 6)

        # Cancel order: stock 6 -> 10
        cancel_resp = self.client.post(f"/api/orders/{order_num}/cancel/")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.json()["status"], "CANCELLED")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

        # Duplicate cancel attempt -> 409 INVALID_TRANSITION
        cancel_again = self.client.post(f"/api/orders/{order_num}/cancel/")
        self.assertEqual(cancel_again.status_code, 409)
        self.assertEqual(cancel_again.json()["error"]["code"], "INVALID_TRANSITION")
