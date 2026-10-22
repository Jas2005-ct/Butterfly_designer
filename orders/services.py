"""
Order stage machine and business logic.

All stage transitions go through advance_stage() so we have a single
choke point for:
- validation (forward/backward rules)
- history recording
- future WhatsApp notifications (seam)
"""

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from customers.models import CustomerMeasurement, MeasurementFields

from .models import (
    Delivery,
    DeliveryLine,
    Order,
    OrderMeasurement,
    OrderStage,
    OrderStageHistory,
)


VALID_NEXT_STAGES = {
    OrderStage.MEASUREMENT: {OrderStage.STITCHING, OrderStage.CANCELLED},
    OrderStage.STITCHING: {OrderStage.READY, OrderStage.MEASUREMENT, OrderStage.CANCELLED},
    OrderStage.READY: {OrderStage.DELIVERED, OrderStage.STITCHING, OrderStage.CANCELLED},
    OrderStage.DELIVERED: {OrderStage.STITCHING},  # rare: return for alteration
    OrderStage.CANCELLED: {OrderStage.MEASUREMENT},  # reopen with note
}

# Canonical lifecycle order (used for display ordering and backward detection).
STAGE_ORDER = [
    OrderStage.MEASUREMENT,
    OrderStage.STITCHING,
    OrderStage.READY,
    OrderStage.DELIVERED,
    OrderStage.CANCELLED,
]

# Emoji per stage for steppers and timelines (tablet-friendly visuals).
# Stage History rows can still hold "cutting"/"fitting" from before the four
# stage model (see orders/0002) - those are rendered by the explicit branches
# in _stage_timeline.html, which stay put as part of the audit trail.
STAGE_EMOJI = {
    OrderStage.MEASUREMENT: "📏",
    OrderStage.STITCHING: "🧵",
    OrderStage.READY: "✅",
    OrderStage.DELIVERED: "📦",
    OrderStage.CANCELLED: "❌",
}


def stage_labels_for(codes):
    """Return [(code, label)] pairs in lifecycle order for the given stage codes.

    Templates unpack {% for code, label in ... %}, so views must pass pairs,
    not raw code strings.
    """
    labels = dict(OrderStage.choices)
    wanted = set(codes)
    return [(code, labels[code]) for code in STAGE_ORDER if code in wanted]


def can_transition(from_stage: str, to_stage: str) -> bool:
    """Pure lifecycle rules: is this move allowed without an admin force?

    Why no role parameter: access control lives in advance_stage() and the
    views. Keeping this function rule-only means the transition map can be
    reasoned about (and tested) without user context.
    """
    if from_stage == to_stage:
        return False
    return to_stage in VALID_NEXT_STAGES.get(from_stage, set())


def advance_stage(
    order: Order,
    to_stage: str,
    changed_by,
    note: str = "",
    force: bool = False,
) -> OrderStageHistory:
    """
    Advance (or move back) an order's stage.

    Args:
        order: Order instance
        to_stage: Target stage (OrderStage value)
        changed_by: User who triggered the change
        note: Optional note (required for backward moves, cancels, admin overrides)
        force: If True, skip validation (admin override)

    Returns:
        The created OrderStageHistory record

    Raises:
        ValueError: If transition is invalid and not forced
    """
    from_stage = order.stage
    changer_is_admin = bool(getattr(getattr(changed_by, "profile", None), "is_admin", False))

    if not can_transition(from_stage, to_stage):
        # Outside the lifecycle map: admin-only, force flag, and note — all three.
        if not changer_is_admin:
            raise ValueError(f"Invalid transition: {from_stage} → {to_stage}")
        if not force:
            raise ValueError(f"Transition {from_stage} → {to_stage} needs an admin force.")
        if not note.strip():
            raise ValueError("Note is required for forced transitions.")

    # Backward moves and cancels require a note
    is_backward = (
        from_stage in STAGE_ORDER
        and to_stage in STAGE_ORDER
        and STAGE_ORDER.index(from_stage) > STAGE_ORDER.index(to_stage)
    )
    is_cancel = to_stage == OrderStage.CANCELLED
    if (is_backward or is_cancel) and not note.strip():
        raise ValueError("Note is required for backward moves and cancellations")

    with transaction.atomic():
        # Update order stage
        order.stage = to_stage
        order.updated_at = timezone.now()
        order.save(update_fields=["stage", "updated_at"])

        # Create history record
        history = OrderStageHistory.objects.create(
            order=order,
            from_stage=from_stage,
            to_stage=to_stage,
            changed_by=changed_by,
            note=note,
        )

    # TODO: Future WhatsApp seam — emit event here
    # notifications.emit_stage_changed(order, history)

    return history


def record_delivery(
    order: Order,
    lines,
    delivered_on=None,
    note: str = "",
    recorded_by=None,
) -> Delivery:
    """Record one hand-over visit, and close the order if it was the last of it.

    Why the single choke point: over-delivery is the one mistake that cannot
    be undone by correcting a number later — the pieces are already with the
    customer. Every path that hands work out (the form today, an API or an
    import later) has to pass through the same check, so it lives here
    rather than in a view.

    Args:
        order: Order receiving the hand-over.
        lines: iterable of (OrderItem, quantity) pairs. Every item must
            belong to `order`; every quantity must be at least 1 and no more
            than what remains of that item across all previous deliveries.
        delivered_on: date of the visit as a datetime.date; defaults to
            today. Accepts None from a blank form field.
        note: free text kept with the visit ("rest on Thursday").
        recorded_by: User recording it; may be None for a system import.

    Returns:
        The created Delivery, with its lines attached.

    Raises:
        ValueError: no lines given; a quantity below 1; an item belonging to
            another order; or a quantity exceeding what remains undelivered
            of that item.
    """
    pairs = [(item, int(quantity)) for item, quantity in lines]

    if not pairs:
        raise ValueError("Add at least one item to this delivery.")

    # What has already gone out of this order, per item, in one query rather
    # than one per line — the 2 -> 4 -> 2 -> 2 case is four visits, and this
    # runs on every one of them.
    already = {
        row["order_item_id"]: row["delivered"]
        for row in DeliveryLine.objects.filter(order_item__order=order)
        .values("order_item_id")
        .annotate(delivered=Sum("quantity"))
    }

    for item, quantity in pairs:
        if item.order_id != order.id:
            raise ValueError("That line does not belong to this order.")
        if quantity < 1:
            raise ValueError("A delivery must hand over at least one piece.")
        remaining = item.quantity - already.get(item.id, 0)
        if quantity > remaining:
            raise ValueError(
                f'Only {remaining} of "{item.description}" remain undelivered.'
            )

    with transaction.atomic():
        delivery = Delivery.objects.create(
            order=order,
            delivered_on=delivered_on or timezone.localdate(),
            note=note,
            recorded_by=recorded_by,
        )
        DeliveryLine.objects.bulk_create(
            [
                DeliveryLine(delivery=delivery, order_item=item, quantity=quantity)
                for item, quantity in pairs
            ]
        )

        # Re-read rather than trusting order.is_fully_delivered: the instance
        # may carry a prefetched deliveries cache that predates the rows just
        # written, and acting on a stale "nothing delivered" here would mean
        # an order the customer has collected stays open forever.
        delivered_now = (
            DeliveryLine.objects.filter(delivery__order=order)
            .aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        total = order.total_quantity
        # The total_quantity guard matters: an order with no lines has
        # nothing outstanding and must never be swept to delivered (see
        # Order.is_fully_delivered).
        if total > 0 and delivered_now >= total:
            # Only when the lifecycle already permits the move. Staff may
            # record deliveries against an order still in stitching, and
            # skipping measurement -> stitching -> ready behind the rules'
            # back would make the stage machine a suggestion.
            if can_transition(order.stage, OrderStage.DELIVERED):
                advance_stage(
                    order,
                    OrderStage.DELIVERED,
                    changed_by=recorded_by,
                    note="All pieces delivered.",
                )

    return delivery


def snapshot_measurements(order):
    """Freeze the customer's measurements onto a new order.

    Why a copy rather than a pointer: a customer is re-measured constantly,
    and an order that silently followed those edits would claim a blouse was
    cut to numbers nobody used. Once written, this row is never touched
    again - it is what the garment was actually made to.

    Why a named function instead of inlining it in the view: order creation
    is currently one view, but the rule "an order remembers what it was cut
    to" deserves a single home, one test, and a name to point at.

    Args:
        order: the just-created Order. Its customer is read for measurements.

    Returns:
        The new OrderMeasurement, or None when the customer has never been
        measured - which is normal and not an error, since plenty of orders
        are taken before anyone puts a tape around anybody.
    """
    source = CustomerMeasurement.objects.filter(customer=order.customer).first()
    if source is None:
        return None

    # Copy only the measurement columns, driven by the shared field list so
    # adding a measurement later cannot leave the snapshot missing it.
    values = {
        attr: getattr(source, attr) for attr, _ in MeasurementFields.MEASUREMENT_LABELS
    }
    return OrderMeasurement.objects.create(order=order, **values)