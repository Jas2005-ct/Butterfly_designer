from decimal import Decimal, InvalidOperation

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import Order, OrderStage, OrderStageHistory, ServiceType
from .services import STAGE_EMOJI, STAGE_ORDER, VALID_NEXT_STAGES, advance_stage, stage_labels_for
from billing.services import annotate_balances, payment_section_context
from butterfly_landing_page.models import About
from customers.models import Customer


def _parse_money(value, field_label):
    """Validate a money input without touching the DB.

    Returns (Decimal|None, error|None). Avoids relying on catching
    DB-layer errors (a failed query poisons the surrounding transaction).
    """
    if value in (None, ""):
        return None, None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None, f"{field_label} must be a number."
    if amount < 0:
        return None, f"{field_label} cannot be negative."
    return amount, None


BOARD_COLUMN_CAP = 10


def _stage_button_context(order, user):
    """Shared context for the _stage_buttons.html partial.

    Why: the partial needs several groupings of the same transition set
    (single primary action vs backward options vs cancel vs admin force).
    Computing them once here keeps the template free of stage logic.

    valid_next_stages is a list of (code, label) pairs in lifecycle order
    (templates unpack two values). valid_next_stage_codes is the raw set
    used for membership tests in the admin-override section.
    """
    user_is_admin = bool(getattr(getattr(user, "profile", None), "is_admin", False))
    codes = set(VALID_NEXT_STAGES.get(order.stage, set()))
    if user_is_admin:
        codes = set(OrderStage.values)
    return {
        "valid_next_stages": stage_labels_for(codes),
        "valid_next_stage_codes": codes,
        "all_stage_choices": OrderStage.choices,
        "user_is_admin": user_is_admin,
    }


def _detail_status_context(order, user):
    """Full status context for the order detail page and HTMX refreshes.

    Why: after an HTMX stage advance the stepper, buttons, and timeline
    must all refresh together — this single builder guarantees the
    full-page render and the partial refresh can never disagree.

    Adds: primary_next (the one happy-path action), backward_options,
    show_cancel, stepper (lifecycle dots), history, days_in_stage.
    """
    context = _stage_button_context(order, user)
    codes = context["valid_next_stage_codes"]
    labels = dict(OrderStage.choices)
    order_idx = STAGE_ORDER.index(order.stage) if order.stage in STAGE_ORDER else -1

    forward = [
        (c, labels[c])
        for c in STAGE_ORDER
        if c in codes and c != OrderStage.CANCELLED and STAGE_ORDER.index(c) > order_idx
    ]
    backward = [
        (c, labels[c])
        for c in STAGE_ORDER
        if c in codes and c != OrderStage.CANCELLED and STAGE_ORDER.index(c) < order_idx
    ]
    context["primary_next"] = forward[0] if forward else None
    context["backward_options"] = backward
    context["show_cancel"] = OrderStage.CANCELLED in codes and order.stage != OrderStage.CANCELLED
    context["is_cancelled"] = order.stage == OrderStage.CANCELLED

    # Sort the prefetched history in Python: calling .order_by() here would
    # issue a fresh query instead of using the prefetched cache.
    history = sorted(order.stage_history.all(), key=lambda h: h.changed_at)
    reached = {h.to_stage for h in history}
    if order.stage != OrderStage.CANCELLED:
        reached.add(order.stage)
    cur_idx = STAGE_ORDER.index(order.stage) if order.stage in STAGE_ORDER else -1
    main_flow = [s for s in STAGE_ORDER if s != OrderStage.CANCELLED]
    stepper = []
    for stage in main_flow:
        if stage == order.stage:
            state = "current"
        elif stage in reached or STAGE_ORDER.index(stage) < cur_idx:
            state = "completed"
        else:
            state = "todo"
        stepper.append({
            "code": stage,
            "label": labels[stage],
            "emoji": STAGE_EMOJI.get(stage, "•"),
            "state": state,
        })
    context["stepper"] = stepper
    context["history"] = history
    since = history[-1].changed_at if history else order.created_at
    context["days_in_stage"] = (timezone.now() - since).days
    return context


@login_required
def board(request):
    """Kanban board: active orders by stage, with Delivered/Cancelled tabs.

    Why tabs: on an 8" tablet 7 columns bury the active work. The default
    Active tab shows the 5 working stages; finished orders are one tap away.
    Cards carry urgency badges (overdue / stuck) so staff can triage
    without opening each order.
    """
    query = request.GET.get("q", "").strip()
    tab = request.GET.get("tab", "active")
    if tab not in ("active", "delivered", "cancelled"):
        tab = "active"
    view_mode = request.GET.get("view", "board")
    if view_mode not in ("board", "list"):
        view_mode = "board"
    show_all = request.GET.get("show") == "all"

    orders_qs = (
        Order.objects.select_related("customer")
        .prefetch_related(
            Prefetch("stage_history", queryset=OrderStageHistory.objects.order_by("changed_at"))
        )
        .order_by("-created_at")
    )

    if query:
        orders_qs = orders_qs.filter(
            Q(order_number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__phone__icontains=query)
        )

    counts = orders_qs.aggregate(
        active=Count("pk", filter=~Q(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])),
        delivered=Count("pk", filter=Q(stage=OrderStage.DELIVERED)),
        cancelled=Count("pk", filter=Q(stage=OrderStage.CANCELLED)),
    )

    if tab == "active":
        visible_stages = [s for s in STAGE_ORDER if s not in (OrderStage.DELIVERED, OrderStage.CANCELLED)]
        orders_qs = orders_qs.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
    elif tab == "delivered":
        visible_stages = [OrderStage.DELIVERED]
        orders_qs = orders_qs.filter(stage=OrderStage.DELIVERED)
    else:
        visible_stages = [OrderStage.CANCELLED]
        orders_qs = orders_qs.filter(stage=OrderStage.CANCELLED)

    # Money per order for the due badge (single GROUP BY, no N+1).
    orders_qs = annotate_balances(orders_qs)

    today = timezone.now().date()
    now = timezone.now()
    labels = dict(OrderStage.choices)
    columns = {stage: [] for stage in visible_stages}
    total_orders = 0
    for order in orders_qs:
        if order.stage in columns:
            hist = list(order.stage_history.all())  # served from prefetch, no extra query
            since = hist[-1].changed_at if hist else order.created_at
            order.days_in_stage = (now - since).days
            order.is_overdue = bool(order.promised_date and order.promised_date < today)
            order.is_stuck = order.days_in_stage > 3
            # One-tap forward step for cards/rows (None when terminal/backward-only).
            order.card_next = None
            order.card_next_label = ""
            if order.stage in STAGE_ORDER:
                order_idx = STAGE_ORDER.index(order.stage)
                forward = [
                    c for c in STAGE_ORDER
                    if c in VALID_NEXT_STAGES.get(order.stage, set())
                    and c != OrderStage.CANCELLED
                    and STAGE_ORDER.index(c) > order_idx
                ]
                if forward:
                    order.card_next = forward[0]
                    order.card_next_label = labels[forward[0]]
            columns[order.stage].append(order)
            total_orders += 1

    # Urgency ordering: overdue first, then oldest first (FIFO).
    # Cap columns so 200+ orders stay light on the tablet DOM.
    column_totals = {}
    remaining_counts = {}
    for stage, cards in columns.items():
        cards.sort(key=lambda o: (not o.is_overdue, o.created_at))
        column_totals[stage] = len(cards)
        if show_all:
            remaining_counts[stage] = 0
        else:
            remaining_counts[stage] = max(0, len(cards) - BOARD_COLUMN_CAP)
            columns[stage] = cards[:BOARD_COLUMN_CAP]

    flat_orders = [o for stage in visible_stages for o in columns[stage]]
    show_all_total = total_orders if not show_all else 0

    return render(
        request,
        "shop/orders/board.html",
        {
            "columns": columns,
            "column_totals": column_totals,
            "remaining_counts": remaining_counts,
            "flat_orders": flat_orders,
            "show_all_link": not show_all and total_orders > BOARD_COLUMN_CAP,
            "show_all_total": show_all_total,
            "stage_order": visible_stages,
            "query": query,
            "tab": tab,
            "view_mode": view_mode,
            "show_all": show_all,
            "counts": counts,
            "service_choices": ServiceType.choices,
            "total_orders": total_orders,
        },
    )


@login_required
@require_POST
def order_card_advance(request, order_id):
    """HTMX: one-tap forward advance from a board card or list row.

    Why no stage parameter: the server always moves to the single valid
    forward-next stage, so a tampered request cannot skip stages, go
    backward, or cancel. Backward/cancel stay on the detail page where a
    reason is collected. Empty body on success: HTMX removes the card/row
    via outerHTML swap; counts + toast update client-side from HX-Trigger.
    """
    order = get_object_or_404(Order, id=order_id)
    from_stage = order.stage
    forward = []
    if from_stage in STAGE_ORDER:
        order_idx = STAGE_ORDER.index(from_stage)
        forward = [
            c for c in STAGE_ORDER
            if c in VALID_NEXT_STAGES.get(from_stage, set())
            and c != OrderStage.CANCELLED
            and STAGE_ORDER.index(c) > order_idx
        ]
    if not forward:
        return HttpResponseBadRequest("No forward move available.")
    try:
        advance_stage(order, forward[0], request.user)
    except ValueError as e:
        return HttpResponseBadRequest(str(e))
    response = HttpResponse("")
    response["HX-Trigger"] = json.dumps({
        "orderMoved": {
            "number": order.order_number,
            "to": order.get_stage_display(),
            "from_stage": from_stage,
            "to_stage": order.stage,
        }
    })
    return response


@login_required
@require_http_methods(["GET", "POST"])
def order_create(request):
    """Create a new order with optional customer selection or inline creation."""
    if request.method == "POST":
        customer_id = request.POST.get("customer")
        customer_name = (request.POST.get("customer_name") or "").strip()
        customer_phone = (request.POST.get("customer_phone") or "").strip()
        service_type = request.POST.get("service_type") or ServiceType.OTHER
        description = (request.POST.get("description") or "").strip()
        total_raw = request.POST.get("total") or "0"
        material_raw = request.POST.get("material_cost") or None
        promised_date = request.POST.get("promised_date") or None
        notes = (request.POST.get("notes") or "").strip()

        total, total_error = _parse_money(total_raw, "Total")
        material_cost, material_error = _parse_money(material_raw, "Material cost")
        if total_error or material_error:
            messages.error(request, total_error or material_error)
            return redirect("orders:create")

        if service_type not in ServiceType.values:
            messages.error(request, "Invalid service type.")
            return redirect("orders:create")

        # Resolve or create customer
        if customer_id:
            customer = get_object_or_404(Customer, id=customer_id)
        elif customer_name and customer_phone:
            customer = Customer.objects.create(
                name=customer_name,
                phone=customer_phone,
                created_by=request.user,
            )
        else:
            messages.error(request, "Select an existing customer or enter name + phone for a new one.")
            return redirect("orders:create")

        try:
            order = Order.objects.create(
                customer=customer,
                service_type=service_type,
                description=description,
                total=total,
                material_cost=material_cost or None,
                promised_date=promised_date or None,
                notes=notes,
                created_by=request.user,
            )
        except ValidationError:
            messages.error(request, "Invalid total, material cost, or date. Please check the values.")
            return redirect("orders:create")
        messages.success(request, f"Order {order.order_number} created.")
        return redirect("orders:detail", order_id=order.id)

    # GET: show form; an existing customer can be preselected via ?customer=<id>.
    selected_customer = None
    preselect_id = request.GET.get("customer")
    if preselect_id:
        selected_customer = Customer.objects.filter(id=preselect_id).first()
    return render(
        request,
        "shop/orders/create.html",
        {
            "selected_customer": selected_customer,
            "service_choices": ServiceType.choices,
        },
    )


@login_required
def order_detail(request, order_id):
    """Order detail with stage buttons (HTMX) and inline payment form (Phase 3)."""
    order = get_object_or_404(
        Order.objects.select_related("customer", "created_by").prefetch_related("items", "stage_history__changed_by"),
        id=order_id,
    )
    context = {"order": order, "shop": About.objects.first()}
    context.update(_detail_status_context(order, request.user))
    context.update(payment_section_context(order))
    return render(request, "shop/orders/detail.html", context)


@login_required
@require_http_methods(["POST"])
def order_advance_stage(request, order_id):
    """HTMX endpoint: advance order stage via POST, return updated stage button partial."""
    order = get_object_or_404(Order, id=order_id)
    to_stage = request.POST.get("to_stage")
    note = (request.POST.get("note") or "").strip()
    requested_force = request.POST.get("force") == "true"

    stage_ctx = _stage_button_context(order, request.user)
    user_is_admin = stage_ctx["user_is_admin"]

    if not to_stage or to_stage not in OrderStage.values:
        context = {"order": order, "error": "Invalid stage specified."}
        context.update(stage_ctx)
        return render(request, "shop/orders/_stage_buttons.html", context, status=400)

    # Only admins may force transitions; everyone else goes through validation.
    force = requested_force and user_is_admin

    try:
        advance_stage(order, to_stage, request.user, note=note, force=force)
    except ValueError as e:
        context = {"order": order, "error": str(e)}
        context.update(_stage_button_context(order, request.user))
        return render(request, "shop/orders/_stage_buttons.html", context, status=400)

    # Re-fetch for updated state. The response swaps the buttons inline
    # and refreshes the stepper + timeline via out-of-band swaps.
    order.refresh_from_db()
    context = {
        "order": order,
        "success": f"Stage updated to {order.get_stage_display()}",
    }
    context.update(_detail_status_context(order, request.user))
    return render(request, "shop/orders/_stage_refresh.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def order_edit(request, order_id):
    """Edit order basic fields."""
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        service_type = request.POST.get("service_type") or order.service_type
        if service_type not in ServiceType.values:
            messages.error(request, "Invalid service type.")
            return redirect("orders:edit", order_id=order.id)
        order.service_type = service_type
        order.description = (request.POST.get("description") or "").strip()
        total_raw = request.POST.get("total")
        material_raw = request.POST.get("material_cost")
        total, total_error = _parse_money(total_raw, "Total") if total_raw else (order.total, None)
        material_cost, material_error = _parse_money(material_raw, "Material cost")
        if total_error or material_error:
            messages.error(request, total_error or material_error)
            return redirect("orders:edit", order_id=order.id)
        order.total = total
        order.material_cost = material_cost
        promised_date = request.POST.get("promised_date")
        order.promised_date = promised_date if promised_date else None
        order.notes = (request.POST.get("notes") or "").strip()
        try:
            order.save()
        except ValidationError:
            messages.error(request, "Invalid total, material cost, or date. Please check the values.")
            return redirect("orders:edit", order_id=order.id)
        messages.success(request, "Order updated.")
        return redirect("orders:detail", order_id=order.id)

    return render(
        request,
        "shop/orders/edit.html",
        {
            "order": order,
            "service_choices": ServiceType.choices,
        },
    )