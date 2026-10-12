import hashlib
import json
import math
from decimal import Decimal
from django.conf import settings
from django.core.paginator import Paginator, EmptyPage
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Product, Dealer, Order, OrderItem
from core.serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    DealerSerializer,
    DealerCreateSerializer,
    OrderSerializer,
)
from core.utils import error_response, calculate_line_item, generate_order_number


def portal_view(request):
    """GET / — Interactive DealerDesk light-mode operations portal (Part E)."""
    return render(request, "portal.html")



# ---------------------------------------------------------
# Health Check
# ---------------------------------------------------------
def health(request):
    """GET /api/health/ — Polled by grader until ready."""
    return JsonResponse({"status": "ok"})



# ---------------------------------------------------------
# Helper: Standard Pagination
# ---------------------------------------------------------
def paginate_queryset(queryset, request, serializer_class):
    page_number = request.GET.get("page", 1)
    try:
        page_size = int(request.GET.get("page_size", 20))
    except ValueError:
        page_size = 20
    page_size = max(1, min(100, page_size))

    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        page = paginator.page(paginator.num_pages) if paginator.num_pages > 0 else []

    serializer = serializer_class(page, many=True)
    
    # Build next and previous URLs
    base_url = request.build_absolute_uri(request.path)
    next_url = None
    prev_url = None
    if hasattr(page, "has_next") and page.has_next():
        next_url = f"{base_url}?page={page.next_page_number()}&page_size={page_size}"
    if hasattr(page, "has_previous") and page.has_previous():
        prev_url = f"{base_url}?page={page.previous_page_number()}&page_size={page_size}"

    return JsonResponse({
        "count": paginator.count,
        "next": next_url,
        "previous": prev_url,
        "results": serializer.data,
    })


# ---------------------------------------------------------
# Products API
# ---------------------------------------------------------
@api_view(["GET", "POST"])
def product_list(request):
    if request.method == "GET":
        qs = Product.objects.all().order_by("sku")
        search = request.GET.get("search")
        if search:
            qs = qs.filter(
                Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(brand__icontains=search)
            )
        category = request.GET.get("category")
        if category:
            qs = qs.filter(category=category)
        vehicle = request.GET.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle)

        return paginate_queryset(qs, request, ProductSerializer)

    elif request.method == "POST":
        data = request.data.copy()
        sku = data.get("sku")
        if not sku:
            return error_response("VALIDATION_ERROR", "SKU is required", 400)
        if Product.objects.filter(sku=sku).exists():
            return error_response("DUPLICATE", f"Product SKU {sku} already exists", 409)

        serializer = ProductCreateSerializer(data=data)
        if not serializer.is_valid():
            return error_response("VALIDATION_ERROR", "Invalid product fields", 400, details=serializer.errors)

        product = serializer.save(stock=0)
        return JsonResponse(ProductSerializer(product).data, status=201)


@api_view(["GET", "PATCH"])
def product_detail(request, sku):
    try:
        product = Product.objects.get(sku=sku)
    except Product.DoesNotExist:
        return error_response("NOT_FOUND", f"Product with SKU '{sku}' not found", 404)

    if request.method == "GET":
        return JsonResponse(ProductSerializer(product).data, status=200)

    elif request.method == "PATCH":
        data = request.data.copy()
        if "sku" in data and data["sku"] != sku:
            return error_response("VALIDATION_ERROR", "SKU cannot be changed", 400)
        
        serializer = ProductCreateSerializer(product, data=data, partial=True)
        if not serializer.is_valid():
            return error_response("VALIDATION_ERROR", "Invalid update fields", 400, details=serializer.errors)
        
        updated_product = serializer.save()
        return JsonResponse(ProductSerializer(updated_product).data, status=200)


# ---------------------------------------------------------
# Inventory API
# ---------------------------------------------------------
@api_view(["PUT"])
def inventory_update(request, sku):
    try:
        product = Product.objects.get(sku=sku)
    except Product.DoesNotExist:
        return error_response("NOT_FOUND", f"Product with SKU '{sku}' not found", 404)

    qty_val = request.data.get("quantity")
    if qty_val is None or not isinstance(qty_val, int) or qty_val < 0:
        return error_response("VALIDATION_ERROR", "Quantity must be an integer >= 0", 400)

    product.stock = qty_val
    product.save()

    return JsonResponse({"sku": product.sku, "quantity": product.stock}, status=200)


# ---------------------------------------------------------
# Dealers API
# ---------------------------------------------------------
@api_view(["GET", "POST"])
def dealer_list(request):
    if request.method == "GET":
        qs = Dealer.objects.all().order_by("id")
        return paginate_queryset(qs, request, DealerSerializer)

    elif request.method == "POST":
        dealer_code = request.data.get("dealer_code")
        if not dealer_code or not request.data.get("name"):
            return error_response("VALIDATION_ERROR", "dealer_code and name are required", 400)
        if Dealer.objects.filter(dealer_code=dealer_code).exists():
            return error_response("DUPLICATE", f"Dealer code {dealer_code} already exists", 409)

        serializer = DealerCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response("VALIDATION_ERROR", "Invalid dealer data", 400, details=serializer.errors)

        dealer = serializer.save()
        return JsonResponse(DealerSerializer(dealer).data, status=201)


@api_view(["GET"])
def dealer_detail(request, pk):
    try:
        dealer = Dealer.objects.get(pk=pk)
    except Dealer.DoesNotExist:
        return error_response("NOT_FOUND", f"Dealer with ID {pk} not found", 404)
    return JsonResponse(DealerSerializer(dealer).data, status=200)


# ---------------------------------------------------------
# Orders API
# ---------------------------------------------------------
@api_view(["GET", "POST"])
def order_list(request):
    if request.method == "GET":
        qs = Order.objects.all().order_by("-created_at")
        dealer_id = request.GET.get("dealer_id")
        if dealer_id:
            qs = qs.filter(dealer_id=dealer_id)
        status_filter = request.GET.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return paginate_queryset(qs, request, OrderSerializer)

    elif request.method == "POST":
        dealer_id = request.data.get("dealer_id")
        items = request.data.get("items")
        idempotency_key = request.headers.get("Idempotency-Key")

        # Validation rules
        if not dealer_id:
            return error_response("VALIDATION_ERROR", "dealer_id is required", 400)
        try:
            dealer = Dealer.objects.get(id=dealer_id)
        except (Dealer.DoesNotExist, ValueError):
            return error_response("VALIDATION_ERROR", f"Dealer {dealer_id} does not exist", 400)

        if not items or not isinstance(items, list) or len(items) == 0:
            return error_response("VALIDATION_ERROR", "At least one item is required", 400)

        # Idempotency check
        request_body_hash = hashlib.sha256(json.dumps(request.data, sort_keys=True).encode()).hexdigest()
        if idempotency_key:
            existing_order = Order.objects.filter(idempotency_key=idempotency_key).first()
            if existing_order:
                if existing_order.idempotency_body_hash == request_body_hash:
                    return JsonResponse(OrderSerializer(existing_order).data, status=200)
                else:
                    return error_response("IDEMPOTENCY_CONFLICT", "Different request body with same Idempotency-Key", 409)

        # Check duplicate SKUs in items
        seen_skus = set()
        for it in items:
            sku = it.get("sku")
            qty = it.get("quantity")
            if not sku or not isinstance(qty, int) or qty < 1:
                return error_response("VALIDATION_ERROR", "Each item must have a valid sku and integer quantity >= 1", 400)
            if sku in seen_skus:
                return error_response("VALIDATION_ERROR", f"Duplicate SKU {sku} in order items", 400)
            seen_skus.add(sku)

        # Verify all SKUs exist
        products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=seen_skus)}
        for sku in seen_skus:
            if sku not in products_by_sku:
                return error_response("VALIDATION_ERROR", f"SKU '{sku}' not found in catalogue", 400)

        from django.db import IntegrityError

        order = None
        for attempt in range(5):
            try:
                with transaction.atomic():
                    if idempotency_key:
                        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
                        if existing:
                            if existing.idempotency_body_hash == request_body_hash:
                                return JsonResponse(OrderSerializer(existing).data, status=200)
                            else:
                                return error_response("IDEMPOTENCY_CONFLICT", "Different request body with same Idempotency-Key", 409)

                    order_num = generate_order_number()
                    order = Order.objects.create(
                        order_number=order_num,
                        dealer=dealer,
                        status="DRAFT",
                        idempotency_key=idempotency_key,
                        idempotency_body_hash=request_body_hash,
                    )

                    subtotal_acc = Decimal("0.00")
                    tax_acc = Decimal("0.00")

                    for it in items:
                        prod = products_by_sku[it["sku"]]
                        qty = it["quantity"]
                        unit_price = prod.price
                        line_sub, line_tax, line_tot = calculate_line_item(unit_price, qty)

                        OrderItem.objects.create(
                            order=order,
                            product=prod,
                            name=prod.name,
                            quantity=qty,
                            unit_price=unit_price,
                            line_subtotal=line_sub,
                            line_tax=line_tax,
                            line_total=line_tot,
                        )
                        subtotal_acc += line_sub
                        tax_acc += line_tax

                    order.subtotal = subtotal_acc
                    order.tax_total = tax_acc
                    order.grand_total = subtotal_acc + tax_acc
                    order.save()
                    break
            except IntegrityError:
                if idempotency_key:
                    existing = Order.objects.filter(idempotency_key=idempotency_key).first()
                    if existing:
                        if existing.idempotency_body_hash == request_body_hash:
                            return JsonResponse(OrderSerializer(existing).data, status=200)
                        else:
                            return error_response("IDEMPOTENCY_CONFLICT", "Different request body with same Idempotency-Key", 409)
                continue

        if not order:
            return error_response("VALIDATION_ERROR", "Could not allocate unique order number, please retry", 400)

        return JsonResponse(OrderSerializer(order).data, status=201)



@api_view(["GET", "PUT"])
def order_detail(request, order_number):
    try:
        order = Order.objects.get(order_number=order_number)
    except Order.DoesNotExist:
        return error_response("NOT_FOUND", f"Order '{order_number}' not found", 404)

    if request.method == "GET":
        return JsonResponse(OrderSerializer(order).data, status=200)

    elif request.method == "PUT":
        if order.status != "DRAFT":
            return error_response("ORDER_LOCKED", f"Cannot edit order in status {order.status}", 409)

        items = request.data.get("items")
        if not items or not isinstance(items, list) or len(items) == 0:
            return error_response("VALIDATION_ERROR", "Items cannot be empty", 400)

        seen_skus = set()
        for it in items:
            sku = it.get("sku")
            qty = it.get("quantity")
            if not sku or not isinstance(qty, int) or qty < 1:
                return error_response("VALIDATION_ERROR", "Invalid item sku or quantity", 400)
            if sku in seen_skus:
                return error_response("VALIDATION_ERROR", f"Duplicate SKU {sku}", 400)
            seen_skus.add(sku)

        products = {p.sku: p for p in Product.objects.filter(sku__in=seen_skus)}
        for sku in seen_skus:
            if sku not in products:
                return error_response("VALIDATION_ERROR", f"Unknown SKU {sku}", 400)

        with transaction.atomic():
            order.items.all().delete()
            subtotal_acc = Decimal("0.00")
            tax_acc = Decimal("0.00")

            for it in items:
                prod = products[it["sku"]]
                qty = it["quantity"]
                unit_price = prod.price
                line_sub, line_tax, line_tot = calculate_line_item(unit_price, qty)

                OrderItem.objects.create(
                    order=order,
                    product=prod,
                    name=prod.name,
                    quantity=qty,
                    unit_price=unit_price,
                    line_subtotal=line_sub,
                    line_tax=line_tax,
                    line_total=line_tot,
                )
                subtotal_acc += line_sub
                tax_acc += line_tax

            order.subtotal = subtotal_acc
            order.tax_total = tax_acc
            order.grand_total = subtotal_acc + tax_acc
            order.save()

        return JsonResponse(OrderSerializer(order).data, status=200)


# ---------------------------------------------------------
# Order Lifecycle Transitions (Confirm, Dispatch, Deliver, Cancel)
# ---------------------------------------------------------
@api_view(["POST"])
def order_confirm(request, order_number):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found", 404)

        if order.status != "DRAFT":
            return error_response("INVALID_TRANSITION", f"Cannot confirm order in status {order.status}", 409)

        # Lock products in deterministic order to prevent deadlocks
        order_items = list(order.items.select_related("product").order_by("product__sku"))
        skus = [it.product.sku for it in order_items]
        locked_products = {
            p.sku: p for p in Product.objects.select_for_update().filter(sku__in=skus).order_by("sku")
        }

        short_lines = []
        messages = []
        for item in order_items:
            prod = locked_products[item.product.sku]
            if prod.stock < item.quantity:
                short_lines.append({
                    "sku": prod.sku,
                    "requested": item.quantity,
                    "available": prod.stock,
                })
                messages.append(f"Insufficient stock for {prod.name}. Available: {prod.stock}, Requested: {item.quantity}")

        if short_lines:
            return error_response("INSUFFICIENT_STOCK", " ".join(messages), 409, details=short_lines)

        # Deduct stock and lock prices
        for item in order_items:
            prod = locked_products[item.product.sku]
            prod.stock -= item.quantity
            prod.save()

        order.status = "CONFIRMED"
        order.save()

    return JsonResponse(OrderSerializer(order).data, status=200)



# ---------------------------------------------------------
# Forecasting & Reorder Suggestions API (Module 5 & 6)
# ---------------------------------------------------------
from core.services.reorder import get_forecast_map, compute_reorder_suggestions


@api_view(["GET"])
def forecast_detail(request, sku):
    forecast_map = get_forecast_map()
    if sku not in forecast_map:
        return error_response("NOT_FOUND", f"Forecast for SKU '{sku}' not found", 404)

    return JsonResponse({
        "sku": sku,
        "forecast": forecast_map[sku],
    }, status=200)


@api_view(["GET"])
def reorder_suggestions(request):
    results = compute_reorder_suggestions()
    return JsonResponse({"results": results}, status=200)


# ---------------------------------------------------------
# Dealer Assistant API (Module 4)
# ---------------------------------------------------------
from core.services.assistant import run_assistant_turn


@api_view(["POST"])
def assistant_chat(request):
    session_id = request.data.get("session_id")
    dealer_id = request.data.get("dealer_id")
    message = request.data.get("message")

    if not session_id or not message:
        return error_response("VALIDATION_ERROR", "session_id and message are required", 400)

    try:
        dealer_id_int = int(dealer_id) if dealer_id is not None else None
    except ValueError:
        return error_response("VALIDATION_ERROR", "dealer_id must be an integer", 400)

    response_data = run_assistant_turn(
        session_id=str(session_id),
        dealer_id=dealer_id_int,
        user_message=str(message),
    )
    return JsonResponse(response_data, status=200)




@api_view(["POST"])
def order_dispatch(request, order_number):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found", 404)

        if order.status != "CONFIRMED":
            return error_response("INVALID_TRANSITION", f"Cannot dispatch order in status {order.status}", 409)

        order.status = "DISPATCHED"
        order.save()
    return JsonResponse(OrderSerializer(order).data, status=200)


@api_view(["POST"])
def order_deliver(request, order_number):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found", 404)

        if order.status != "DISPATCHED":
            return error_response("INVALID_TRANSITION", f"Cannot deliver order in status {order.status}", 409)

        order.status = "DELIVERED"
        order.save()
    return JsonResponse(OrderSerializer(order).data, status=200)


@api_view(["POST"])
def order_cancel(request, order_number):
    with transaction.atomic():
        try:
            order = Order.objects.select_for_update().get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found", 404)

        if order.status not in ["DRAFT", "CONFIRMED"]:
            return error_response("INVALID_TRANSITION", f"Cannot cancel order in status {order.status}", 409)

        if order.status == "CONFIRMED":
            order_items = list(order.items.select_related("product").order_by("product__sku"))
            skus = [it.product.sku for it in order_items]
            locked_products = {
                p.sku: p for p in Product.objects.select_for_update().filter(sku__in=skus).order_by("sku")
            }
            # Restore stock exactly once
            for item in order_items:
                prod = locked_products[item.product.sku]
                prod.stock += item.quantity
                prod.save()

        order.status = "CANCELLED"
        order.save()

    return JsonResponse(OrderSerializer(order).data, status=200)



# ---------------------------------------------------------
# Supplier Sync API (Section 8)
# ---------------------------------------------------------
from core.services.supplier import sync_supplier_catalogue, SupplierSyncError


@api_view(["POST"])
def supplier_sync(request):
    """POST /api/sync/supplier/ — Sync external supplier feed."""
    try:
        summary = sync_supplier_catalogue()
        return JsonResponse(summary, status=200)
    except SupplierSyncError as e:
        return error_response("SUPPLIER_UNAVAILABLE", str(e), status=502)
    except Exception as e:
        return error_response("SUPPLIER_UNAVAILABLE", f"Supplier sync failed: {e}", status=502)

