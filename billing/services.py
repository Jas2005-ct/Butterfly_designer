"""Billing calculations and payment recording.

Why a service layer: balance = total − Σ payments must be computed
identically on the order detail page, the board badge, and the dues
report. One helper prevents the three pages drifting apart, and
record_payment() is the single choke point where money validation lives.

Record-only in v1 (no edit/delete): a wrong entry is corrected with an
offsetting payment + note, so every rupee stays attributable.
"""

from decimal import Decimal, InvalidOperation

from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce

from .forms import PaymentForm
from .models import Payment, PaymentMode


def total_paid(order):
    """Return the sum of all payments recorded against an order.

    Why: the single source of truth for "how much has the customer paid".
    Returns Decimal("0") when nothing was recorded yet.
    """
    result = order.payments.aggregate(total=Sum("amount"))["total"]
    return result if result is not None else Decimal("0")


def order_balance(order):
    """Return total − paid (positive = due, zero = settled, negative = overpaid).

    Why: every money display in the app derives from this one function.
    """
    total = order.total if order.total is not None else Decimal("0")
    return total - total_paid(order)


def record_payment(order, amount, mode, recorded_by, note=""):
    """Record a payment against an order.

    Why: the only sanctioned way to add money movement — validation here
    protects both the HTMX form and any future caller (import, API).

    Args:
        order: Order the payment belongs to.
        amount: Positive number-like (str/Decimal/int accepted).
        mode: One of PaymentMode values (cash/upi/card/other).
        recorded_by: User taking the payment (accountability trail).
        note: Optional reference (e.g. UPI transaction id).

    Returns:
        The created Payment instance.

    Raises:
        ValueError: If amount is not a positive number or mode is invalid.
    """
    try:
        amount = Decimal(str(amount))
    except InvalidOperation:
        raise ValueError("Amount must be a number.")
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if mode not in PaymentMode.values:
        raise ValueError("Invalid payment mode.")
    return Payment.objects.create(
        order=order,
        amount=amount,
        mode=mode,
        recorded_by=recorded_by,
        note=(note or "").strip(),
    )


def annotate_balances(queryset):
    """Annotate each order with paid (Σ payments) and balance (total − paid).

    Why: board badges and the dues report need per-order money without
    N+1 queries — one GROUP BY serves the whole list.
    """
    # Two annotate() steps: referencing the paid alias inside the same call
    # works today but is fragile — the second call sees a resolved alias.
    return queryset.annotate(
        paid=Coalesce(
            Sum("payments__amount"),
            Decimal("0"),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    ).annotate(
        balance=F("total") - F("paid"),
    )


def payment_section_context(order):
    """Context for the payments partial on the order detail page.

    Why: the full-page render and the HTMX add-payment response must show
    identical figures — both build from this one function.
    """
    payments = order.payments.select_related("recorded_by").order_by("-created_at")
    paid = total_paid(order)
    total = order.total if order.total is not None else Decimal("0")
    return {
        "order": order,
        "payments": payments,
        "payment_form": PaymentForm(),
        "payment_modes": PaymentMode.choices,
        "paid": paid,
        "balance": total - paid,
    }
