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

    Why one function: every money display in the app derives from it, so the
    board badge, the order page and the dues report can never disagree.

    Why None rather than 0 when no total has been agreed: 0 reads as "settled
    in full", which is exactly backwards for an order whose price is still
    open. Callers must treat None as "not counted yet" — the board badges it
    as Amount TBD and the dues report files it under its own heading rather
    than quietly dropping it.

    Args:
        order: the Order to price up. Its total may be None.

    Returns:
        Decimal, or None when no total has been agreed for this order.
    """
    if order.total is None:
        return None
    return order.total - total_paid(order)


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

    balance comes out as None when total is NULL, which is the answer
    order_balance() gives for the same order — the SQL simply carries the
    NULL through instead of inventing a zero. Templates therefore have to
    guard before comparing: `None > 0` raises rather than being falsy.
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

    balance follows order_balance(), so it is None when the order has no
    total yet. The template says so rather than printing ₹0.00 for a price
    nobody has agreed.
    """
    payments = order.payments.select_related("recorded_by").order_by("-created_at")
    return {
        "order": order,
        "payments": payments,
        "payment_form": PaymentForm(),
        "payment_modes": PaymentMode.choices,
        "paid": total_paid(order),
        "balance": order_balance(order),
    }
