from accounts.decorators import admin_required
from django.shortcuts import render

from . import services


@admin_required
def dashboard(request):
    """Owner analytics dashboard (admin-only).

    Why one view assembling service outputs: each figure keeps its
    definition in services.py; this view only picks windows (14 days,
    8 weeks, 6 months, top 5) and scales bars for the CSS charts.
    """
    daily = services.revenue_by_day(days=14)
    weekly = services.revenue_by_week(weeks=8)
    monthly = services.revenue_by_month(months=6)
    profit = services.profit_totals()
    stages = services.orders_by_stage()
    mix = services.service_mix()
    stuck = services.stuck_orders()
    top = services.top_customers(limit=5)
    counts = services.pipeline_counts()

    day_max = max([row["total"] for row in daily] + [0])
    for row in daily:
        row["width"] = services.bar_width(row["total"], day_max)
    week_max = max([row["total"] for row in weekly] + [0])
    for row in weekly:
        row["width"] = services.bar_width(row["total"], week_max)
    month_max = max([row["total"] for row in monthly] + [0])
    for row in monthly:
        row["width"] = services.bar_width(row["total"], month_max)
    mix_max = max([row["revenue"] for row in mix] + [0])
    for row in mix:
        row["width"] = services.bar_width(row["revenue"], mix_max)

    return render(
        request,
        "shop/analytics/dashboard.html",
        {
            "revenue_total": services.revenue_total(),
            "revenue_30d": services.revenue_since(30),
            "outstanding": services.total_outstanding(),
            "profit": profit,
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
            "stages": stages,
            "mix": mix,
            "stuck": stuck,
            "top": top,
            "counts": counts,
        },
    )
