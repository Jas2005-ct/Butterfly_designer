"""Analytics queries for the owner dashboard.

Why a service layer: every figure on /shop/analytics/ must have one
definition. These helpers also pin down business meanings:

- Revenue = money actually received (Payment rows), not order totals.
  An order contributes to revenue only when cash/UPI/card lands.
- Profit = Σ order totals − Σ material costs over non-cancelled orders.
  It is "potential + realized" margin; material_cost may be blank and
  then counts as zero (conservative: profit looks bigger — the dashboard
  labels this).
- Stuck = open order unchanged for more than STUCK_AFTER_DAYS (locked: 3).
- Open = any stage except delivered/cancelled.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.models import Payment
from customers.models import Customer
from orders.models import Order, OrderStage, ServiceType

STUCK_AFTER_DAYS = 3


def _money(value):
    """Return value or Decimal("0") when the aggregate found no rows."""
    return value if value is not None else Decimal("0")


def revenue_total():
    """All money received to date (sum of every payment)."""
    return _money(Payment.objects.aggregate(total=Sum("amount"))["total"])


def revenue_since(days):
    """Money received in the trailing N days (IST-aware)."""
    cutoff = timezone.now() - timedelta(days=days)
    return _money(
        Payment.objects.filter(created_at__gte=cutoff).aggregate(total=Sum("amount"))["total"]
    )


def _payments_in_window(since):
    """Payments from `since`, grouped by local (IST) date in Python.

    Why Python, not TruncDate/Week/Month: DB truncation uses UTC, so a
    payment at 00:30 IST lands on "yesterday". Grouping by localdate is
    correct on every backend and trivial at shop scale.
    """
    by_day = {}
    for created_at, amount in Payment.objects.filter(created_at__gte=since).values_list(
        "created_at", "amount"
    ):
        day = timezone.localdate(created_at)
        by_day[day] = by_day.get(day, Decimal("0")) + amount
    return by_day


def revenue_by_day(days=14):
    """Per-day revenue for the trailing N days, oldest first.

    Why a list of dicts (not a queryset): the template bar chart needs
    plain label/value pairs including zero-revenue days.
    """
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    by_day = _payments_in_window(timezone.now() - timedelta(days=days))
    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        series.append({"label": day.strftime("%d %b"), "total": by_day.get(day) or Decimal("0")})
    return series


def revenue_by_week(weeks=8):
    """Per-week revenue (Monday-start, local dates) for the trailing N weeks."""
    today = timezone.localdate()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=weeks - 1)
    by_day = _payments_in_window(timezone.now() - timedelta(weeks=weeks))
    series = []
    for index in range(weeks):
        monday = start + timedelta(weeks=index)
        total = Decimal("0")
        for offset in range(7):
            total += by_day.get(monday + timedelta(days=offset)) or Decimal("0")
        series.append({"label": monday.strftime("%d %b"), "total": total})
    return series


def revenue_by_month(months=6):
    """Per-month revenue for the trailing N months, oldest first."""
    today = timezone.localdate()
    by_day = _payments_in_window(timezone.now() - timedelta(days=31 * months))
    by_month = {}
    for day, total in by_day.items():
        key = (day.year, day.month)
        by_month[key] = by_month.get(key, Decimal("0")) + total
    series = []
    year, month = today.year, today.month
    for _ in range(months):
        # Build newest-first, reverse at the end.
        series.append(
            {
                "label": f"{year}-{month:02d}",
                "total": by_month.get((year, month)) or Decimal("0"),
            }
        )
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(series))


def profit_totals():
    """Revenue-booked profit: Σ totals − Σ material costs (non-cancelled).

    Why non-cancelled (not just delivered): the owner wants margin on the
    live pipeline too. Material cost blank = 0 (dashboard labels this).
    """
    rows = Order.objects.exclude(stage=OrderStage.CANCELLED).aggregate(
        revenue=Coalesce(Sum("total"), Decimal("0")),
        material=Coalesce(Sum("material_cost"), Decimal("0")),
    )
    revenue = rows["revenue"]
    material = rows["material"]
    return {"revenue": revenue, "material": material, "profit": revenue - material}


def orders_by_stage():
    """Open-pipeline counts per stage, in lifecycle order."""
    from orders.services import STAGE_ORDER

    rows = Order.objects.values("stage").annotate(count=Count("id"))
    by_stage = {row["stage"]: row["count"] for row in rows}
    labels = dict(OrderStage.choices)
    return [
        {"stage": stage, "label": labels[stage], "count": by_stage.get(stage, 0)}
        for stage in STAGE_ORDER
    ]


def service_mix():
    """Order counts + booked revenue per service type (non-cancelled)."""
    rows = (
        Order.objects.exclude(stage=OrderStage.CANCELLED)
        .values("service_type")
        .annotate(count=Count("id"), revenue=Coalesce(Sum("total"), Decimal("0")))
        .order_by("-revenue")
    )
    labels = dict(ServiceType.choices)
    return [
        {
            "service": row["service_type"],
            "label": labels.get(row["service_type"], row["service_type"]),
            "count": row["count"],
            "revenue": row["revenue"],
        }
        for row in rows
    ]


def stuck_orders():
    """Open orders unchanged for more than STUCK_AFTER_DAYS, oldest first.

    Why Python-side (not a query): "unchanged since" = latest history
    entry or creation when untouched — a per-order max that is clearer
    (and fast enough at shop scale) in Python over one prefetch.
    """
    now = timezone.now()
    cutoff = now - timedelta(days=STUCK_AFTER_DAYS)
    candidates = (
        Order.objects.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
        .select_related("customer")
        .prefetch_related("stage_history")
        .order_by("created_at")
    )
    stuck = []
    for order in candidates:
        # Prefetch order is undefined: take the latest change explicitly.
        changes = [h.changed_at for h in order.stage_history.all()]
        since = max(changes) if changes else order.created_at
        if since < cutoff:
            order.stuck_days = (now - since).days
            stuck.append(order)
    stuck.sort(key=lambda o: o.stuck_days, reverse=True)
    return stuck


def top_customers(limit=5):
    """Top customers by booked revenue (non-cancelled orders)."""
    return list(
        Customer.objects.annotate(
            revenue=Coalesce(
                Sum("orders__total", filter=~Q(orders__stage=OrderStage.CANCELLED)),
                Decimal("0"),
            ),
            order_count=Count("orders", filter=~Q(orders__stage=OrderStage.CANCELLED)),
        )
        .filter(order_count__gt=0)
        .order_by("-revenue")[:limit]
    )


def pipeline_counts():
    """Delivered vs open vs cancelled counts for the headline strip."""
    rows = Order.objects.values("stage").annotate(count=Count("id"))
    by_stage = {row["stage"]: row["count"] for row in rows}
    delivered = by_stage.get(OrderStage.DELIVERED, 0)
    cancelled = by_stage.get(OrderStage.CANCELLED, 0)
    total = sum(by_stage.values())
    return {
        "delivered": delivered,
        "cancelled": cancelled,
        "open": total - delivered - cancelled,
        "total": total,
    }


def total_outstanding():
    """Sum of positive balances on open orders (collection target)."""
    from billing.services import annotate_balances

    rows = (
        annotate_balances(
            Order.objects.exclude(stage__in=[OrderStage.DELIVERED, OrderStage.CANCELLED])
        )
        .filter(balance__gt=0)
        .aggregate(total=Sum("balance"))
    )
    return _money(rows["total"])


def bar_width(value, maximum):
    """CSS width % for bar charts (0 when there is nothing to scale)."""
    if not maximum:
        return 0
    return round(float(value) / float(maximum) * 100)
