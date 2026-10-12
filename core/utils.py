from decimal import Decimal, ROUND_HALF_UP
from django.http import JsonResponse
from django.utils import timezone
import zoneinfo
from core.models import Order


def error_response(code: str, message: str, status: int = 400, details=None) -> JsonResponse:
    """Return error in standard DealerDesk shape:
    {"error": {"code": code, "message": message, "details": details or []}}
    """
    if details is None:
        details = []
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        },
        status=status,
    )


def calculate_line_item(unit_price: Decimal, quantity: int):
    """Calculates subtotal, 18% GST with ROUND_HALF_UP, and total for a single order line."""
    two_places = Decimal("0.01")
    line_subtotal = (unit_price * quantity).quantize(two_places, rounding=ROUND_HALF_UP)
    line_tax = (line_subtotal * Decimal("0.18")).quantize(two_places, rounding=ROUND_HALF_UP)
    line_total = line_subtotal + line_tax
    return line_subtotal, line_tax, line_total


def generate_order_number() -> str:
    """Generates ORD-YYYYMMDD-NNNN in Asia/Kolkata timezone."""
    kolkata_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    now_kolkata = timezone.now().astimezone(kolkata_tz)
    date_str = now_kolkata.strftime("%Y%m%d")
    prefix = f"ORD-{date_str}-"

    # Find highest sequence today
    today_orders = Order.objects.filter(order_number__startswith=prefix).order_by("-order_number")
    last_order = today_orders.first()
    if last_order:
        try:
            last_seq = int(last_order.order_number.split("-")[-1])
            seq = last_seq + 1
        except ValueError:
            seq = today_orders.count() + 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"
