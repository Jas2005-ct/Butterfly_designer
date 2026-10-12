from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from orders.models import Order, OrderStage
from .forms import PaymentForm
from .services import annotate_balances, payment_section_context, record_payment


@login_required
def dues(request):
    """Dues report: open orders with a positive balance, sortable.

    Why: this is the "who owes us money" list the owner checks daily.
    Delivered/cancelled orders are excluded — their money is handled at
    handover, not as dues. Fully-paid open orders are excluded too.
    """
    sort = request.GET.get("sort", "promised")
    orders_qs = (
        Order.objects.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
        .select_related("customer")
    )
    orders_qs = annotate_balances(orders_qs).filter(balance__gt=0)

    sort_options = {
        "promised": F("promised_date").asc(nulls_last=True),
        "balance": "-balance",
        "customer": "customer__name",
        "oldest": "created_at",
    }
    ordering = sort_options.get(sort, sort_options["promised"])
    if sort not in sort_options:
        sort = "promised"
    orders_qs = orders_qs.order_by(ordering)

    outstanding = orders_qs.aggregate(total=Sum("balance"))["total"]
    if outstanding is None:
        outstanding = Decimal("0")

    return render(
        request,
        "shop/billing/dues.html",
        {
            "orders": orders_qs[:200],
            "sort": sort,
            "total_outstanding": outstanding,
        },
    )


@login_required
@require_POST
def order_payment_add(request, order_id):
    """HTMX endpoint: record a payment, return the refreshed payments partial.

    Why HTMX + partial: counter staff record advances rapid-fire without
    losing the order detail scroll position to a full reload.
    """
    order = get_object_or_404(Order, id=order_id)
    form = PaymentForm(request.POST)
    if not form.is_valid():
        context = payment_section_context(order)
        context["payment_form"] = form
        context["error"] = "Please fix the highlighted fields."
        return render(request, "shop/billing/_payments.html", context, status=400)

    try:
        record_payment(
            order,
            form.cleaned_data["amount"],
            form.cleaned_data["mode"],
            request.user,
            note=form.cleaned_data["note"],
        )
    except ValueError as e:
        context = payment_section_context(order)
        context["error"] = str(e)
        return render(request, "shop/billing/_payments.html", context, status=400)

    order.refresh_from_db()
    context = payment_section_context(order)
    context["success"] = "Payment recorded."
    return render(request, "shop/billing/_payments.html", context)
