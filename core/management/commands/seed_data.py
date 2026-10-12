import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Product, Dealer


class Command(BaseCommand):
    help = "Load catalogue.csv and dealers.csv into the database idempotently."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR  # <repo root>/data
        self.stdout.write(f"Seeding data from: {data_dir}")

        catalogue_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not catalogue_path.exists():
            self.stderr.write(self.style.ERROR(f"Catalogue file not found at {catalogue_path}"))
            return
        if not dealers_path.exists():
            self.stderr.write(self.style.ERROR(f"Dealers file not found at {dealers_path}"))
            return

        with transaction.atomic():
            # Seed Products
            products_created = 0
            products_updated = 0
            with open(catalogue_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sku = row["sku"].strip()
                    price = Decimal(row["price"].strip())
                    stock = int(row["stock"].strip()) if "stock" in row and row["stock"].strip() else 0
                    
                    obj, created = Product.objects.update_or_create(
                        sku=sku,
                        defaults={
                            "name": row["name"].strip(),
                            "category": row["category"].strip(),
                            "brand": row["brand"].strip(),
                            "vehicle_fitment": row.get("vehicle_fitment", "").strip(),
                            "price": price,
                            "stock": stock,
                            "description": row.get("description", "").strip(),
                        },
                    )
                    if created:
                        products_created += 1
                    else:
                        products_updated += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"Products seeded: {products_created} created, {products_updated} updated (Total: {Product.objects.count()})"
                )
            )

            # Seed Dealers
            dealers_created = 0
            dealers_updated = 0
            with open(dealers_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dealer_code = row["dealer_code"].strip()
                    credit_limit = Decimal(row.get("credit_limit", "0.00").strip() or "0.00")

                    obj, created = Dealer.objects.update_or_create(
                        dealer_code=dealer_code,
                        defaults={
                            "name": row["name"].strip(),
                            "city": row.get("city", "").strip(),
                            "phone": row.get("phone", "").strip(),
                            "email": row.get("email", "").strip(),
                            "gstin": row.get("gstin", "").strip(),
                            "credit_limit": credit_limit,
                        },
                    )
                    if created:
                        dealers_created += 1
                    else:
                        dealers_updated += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"Dealers seeded: {dealers_created} created, {dealers_updated} updated (Total: {Dealer.objects.count()})"
                )
            )
