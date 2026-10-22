from django.shortcuts import get_object_or_404, render

from orders.models import Order, OrderStage


def track_order(request, token):
    """Public order tracking page — no login required."""
    order = get_object_or_404(
        Order.objects.select_related("customer").prefetch_related("stage_history__changed_by"),
        track_token=token,
    )

    # Build milestone data for the chart
    stage_order = [
        OrderStage.MEASUREMENT,
        OrderStage.STITCHING,
        OrderStage.READY,
        OrderStage.DELIVERED,
    ]

    # Index in-memory over the prefetched history (avoids a query per stage).
    history_by_stage = {}
    for entry in order.stage_history.all():
        history_by_stage.setdefault(entry.to_stage, entry)

    current_index = stage_order.index(order.stage) if order.stage in stage_order else -1

    milestones = []
    for stage in stage_order:
        history_entry = history_by_stage.get(stage)
        is_current = order.stage == stage
        if order.stage == OrderStage.CANCELLED:
            # Cancelled: only stages actually reached (have history) count as completed.
            is_completed = history_entry is not None
        else:
            is_completed = stage_order.index(stage) < current_index or history_entry is not None
        milestones.append({
            "stage": stage,
            "label": dict(OrderStage.choices).get(stage, stage),
            "completed": is_completed,
            "current": is_current,
            "timestamp": history_entry.changed_at if history_entry else None,
            "note": history_entry.note if history_entry else "",
        })

    # Handle cancelled separately
    cancelled_entry = history_by_stage.get(OrderStage.CANCELLED)
    is_cancelled = order.stage == OrderStage.CANCELLED
    milestones.append({
        "stage": OrderStage.CANCELLED,
        "label": "Cancelled",
        "completed": is_cancelled,
        "current": is_cancelled,
        "timestamp": cancelled_entry.changed_at if cancelled_entry else None,
        "note": cancelled_entry.note if cancelled_entry else "",
    })

    return render(
        request,
        "shop/orders/track.html",
        {
            "order": order,
            "milestones": milestones,
            "is_delivered": order.stage == OrderStage.DELIVERED,
            "is_cancelled": is_cancelled,
        },
    )