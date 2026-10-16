"""
Order stage machine and business logic.

All stage transitions go through advance_stage() so we have a single
choke point for:
- validation (forward/backward rules)
- history recording
- future WhatsApp notifications (seam)
"""

from django.db import transaction
from django.utils import timezone

from .models import Order, OrderStage, OrderStageHistory


VALID_NEXT_STAGES = {
    OrderStage.MEASUREMENT: {OrderStage.CUTTING, OrderStage.CANCELLED},
    OrderStage.CUTTING: {OrderStage.STITCHING, OrderStage.MEASUREMENT, OrderStage.CANCELLED},
    OrderStage.STITCHING: {OrderStage.FITTING, OrderStage.CUTTING, OrderStage.CANCELLED},
    OrderStage.FITTING: {OrderStage.READY, OrderStage.STITCHING, OrderStage.CANCELLED},
    OrderStage.READY: {OrderStage.DELIVERED, OrderStage.FITTING, OrderStage.CANCELLED},
    OrderStage.DELIVERED: {OrderStage.FITTING},  # rare: return for alteration
    OrderStage.CANCELLED: {OrderStage.MEASUREMENT},  # reopen with note
}

# Canonical lifecycle order (used for display ordering and backward detection).
STAGE_ORDER = [
    OrderStage.MEASUREMENT,
    OrderStage.CUTTING,
    OrderStage.STITCHING,
    OrderStage.FITTING,
    OrderStage.READY,
    OrderStage.DELIVERED,
    OrderStage.CANCELLED,
]

# Emoji per stage for steppers and timelines (tablet-friendly visuals).
STAGE_EMOJI = {
    OrderStage.MEASUREMENT: "📏",
    OrderStage.CUTTING: "✂️",
    OrderStage.STITCHING: "🧵",
    OrderStage.FITTING: "👗",
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