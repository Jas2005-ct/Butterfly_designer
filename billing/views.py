from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from orders.models import Order, OrderStage
from .forms import PaymentForm
from .models import PaymentKind, PaymentMode
from .services import (
    annotate_balances,
    order_balance,
    payment_section_context,
    record_payment,
)


@login_required
def dues(request):
    """Dues report: open orders with a positive balance, sortable.

    Why: this is the "who owes us money" list the owner checks daily.
    Delivered/cancelled orders are excluded — their money is handled at
    handover, not as dues. Fully-paid open orders are excluded too.

    Orders with no agreed price fall out of the balance list by themselves,
    since their balance is None and None is not greater than zero. That is
    correct for the money total but would hide the order from the report
    entirely, so they get their own "Amount not set" group — an unpriced
    order is precisely the one most likely to be forgotten.
    """
    sort = request.GET.get("sort", "promised")
    open_orders = annotate_balances(
        Order.objects.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
        .select_related("customer")
    )

    sort_options = {
        "promised": F("promised_date").asc(nulls_last=True),
        "balance": "-balance",
        "customer": "customer__name",
        "oldest": "created_at",
    }
    ordering = sort_options.get(sort, sort_options["promised"])
    if sort not in sort_options:
        sort = "promised"

    orders_qs = open_orders.filter(balance__gt=0).order_by(ordering)
    # Same ordering as the main list for consistency. Under "Highest due" it
    # amounts to nothing here — every row's balance is NULL — which is honest,
    # since an unpriced order has no due to rank by.
    unpriced_qs = open_orders.filter(total__isnull=True).order_by(ordering)

    outstanding = orders_qs.aggregate(total=Sum("balance"))["total"]
    if outstanding is None:
        outstanding = Decimal("0")

    return render(
        request,
        "shop/billing/dues.html",
        {
            "orders": orders_qs[:200],
            "unpriced": unpriced_qs[:200],
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

    Why the delivery is re-scoped to the order here rather than fetched by id:
    the picker only ever offers this order's hand-overs, so a stray id can only
    arrive in a hand-built POST. Looking it up inside order.deliveries turns
    that into a plain "not yours" instead of a payment filed against another
    customer's hand-over.
    """
    order = get_object_or_404(Order, id=order_id)
    form = PaymentForm(request.POST, order=order)

    def rejected(message):
        """Re-render the panel at 400 with the payment as it was typed.

        One body instead of three, so the bind-the-form rule is structural
        rather than remembered per rejection site: the form-level errors,
        a hand-built POST naming another order's delivery, and the service
        layer's ValueError (unreachable from browser traffic - the Form
        co-validates kind/delivery - but the choke point guards callers
        that skip the Form) all re-render the same bound form. A fresh
        PaymentForm here would blank the panel on the very POST that
        finally told staff the amount was wrong.
        """
        context = payment_section_context(order)
        context["payment_form"] = form
        context["error"] = message
        return render(request, "shop/billing/_payments.html", context, status=400)

    if not form.is_valid():
        return rejected("Please fix the highlighted fields.")

    delivery = None
    delivery_id = form.cleaned_data.get("delivery")
    if delivery_id:
        delivery = order.deliveries.filter(id=delivery_id).first()
        if delivery is None:
            return rejected("That delivery does not belong to this order.")

    try:
        record_payment(
            order,
            form.cleaned_data["amount"],
            form.cleaned_data["mode"],
            request.user,
            note=form.cleaned_data["note"],
            kind=form.cleaned_data["kind"],
            delivery=delivery,
        )
    except ValueError as e:
        return rejected(str(e))

    order.refresh_from_db()
    context = payment_section_context(order)
    context["success"] = "Payment recorded."
    return render(request, "shop/billing/_payments.html", context)


@login_required
@require_POST
def order_settle_balance(request, order_id):
    """One-tap settlement: receive exactly what is still due, at hand-over.

    Why the amount is never read from the POST: the balance the panel
    rendered may be stale - another window, or the same counter earlier, can
    have recorded a payment in between - so the figure is recomputed here,
    at the moment of recording, and the tap collects what is really left.
    Charging whatever the stale screen showed could over-collect.

    Why the kind and delivery are decided here rather than asked: a payment
    taken at a hand-over is an Against-delivery payment naming that
    hand-over; when the order was never delivered through the app, there is
    no hand-over to name, and Advance is the only remaining truth. Asking
    staff to make that distinction would defeat the one-tap point, and both
    chalk the same rupees onto the balance.

    Why this never blocks anything: when the balance is already nil - or the
    price was never agreed - there is nothing to settle, and the panel says
    so instead of erroring. Recording it and the stage move are independent
    actions a staff member takes when each actually happens.

    Args:
        request: the POST; a POST carrying the HX-Request header (every
            rendered button does) gets the refreshed partial back so the
            counter never loses its scroll, anything else is redirected to
            the panel with a message.
        order_id: the order being settled.

    Returns:
        HttpResponse. The refreshed payments partial on success, or a
        redirect/re-render with the reason when there is nothing to settle
        or the payment mode is unrecognisable.
    """
    order = get_object_or_404(Order, id=order_id)

    def answer(order, message, ok=True):
        """Reply to both clients the panel knows about.

        The buttons ship with hx-post, so browsers with the page's own
        script loaded swap the partial in place. A POST that arrives without
        that header (script absent, or hand-built) is answered like every
        other no-JS action here: a redirect to the panel, carrying the
        message with it.
        """
        order.refresh_from_db()
        context = payment_section_context(order)
        context["success" if ok else "error"] = message
        if request.headers.get("HX-Request"):
            response = render(request, "shop/billing/_payments.html", context)
            if not ok:
                response.status_code = 400
            return response
        (messages.success if ok else messages.error)(request, message)
        return redirect(reverse("orders:detail", args=[order.id]) + "#order-payments")

    mode = request.POST.get("mode")
    if mode not in PaymentMode.values:
        return answer(order, "Choose how the money arrived.", ok=False)

    # Recomputed, not trusted: settle what is due NOW (see docstring).
    due = order_balance(order)
    if due is None or due <= 0:
        return answer(order, "Nothing left to settle.")

    # The most recent hand-over is the one being paid for. Delivery orders
    # itself by (delivered_on, created_at), so .last() is the visit just
    # recorded - exactly the one a counter payment belongs to, and the
    # ledger can name it honestly.
    delivery = order.deliveries.order_by("delivered_on", "created_at").last()
    kind = PaymentKind.DELIVERY if delivery is not None else PaymentKind.ADVANCE

    try:
        record_payment(order, due, mode, request.user, kind=kind, delivery=delivery)
    except ValueError as e:
        # Unreachable from the rendered buttons (mode is validated above,
        # the amount comes from the order itself); the choke point still
        # guards callers that skip this view.
        return answer(order, str(e), ok=False)

    _mode_labels = dict(PaymentMode.choices)
    return answer(
        order,
        f"₹{due} received in {_mode_labels[mode]}.",
    )
