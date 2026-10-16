from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import PaymentMode
from billing.services import record_payment
from customers.models import Customer
from orders.models import Order, OrderStage
from orders.services import advance_stage
from . import services


class RevenueTests(TestCase):
    """Revenue = money received (payments), never order totals."""

    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")
        self.order = Order.objects.create(customer=self.customer, total=Decimal("1000.00"))

    def test_revenue_counts_payments_not_totals(self):
        self.assertEqual(services.revenue_total(), Decimal("0"))
        record_payment(self.order, "400", PaymentMode.CASH, self.user)
        self.assertEqual(services.revenue_total(), Decimal("400"))
        # Order total alone (no payment) contributes nothing.
        Order.objects.create(customer=self.customer, total=Decimal("5000.00"))
        self.assertEqual(services.revenue_total(), Decimal("400"))

    def test_revenue_since_window(self):
        record_payment(self.order, "400", PaymentMode.CASH, self.user)
        self.assertEqual(services.revenue_since(30), Decimal("400"))
        self.assertEqual(services.revenue_since(0), Decimal("0"))

    def test_revenue_by_day_length_and_sums(self):
        record_payment(self.order, "250", PaymentMode.UPI, self.user)
        series = services.revenue_by_day(days=14)
        self.assertEqual(len(series), 14)
        self.assertEqual(sum(row["total"] for row in series), Decimal("250"))
        # Local date, not timezone.now() wall time: now() is UTC and would
        # give "yesterday" during IST late-night hours.
        today_label = timezone.localdate().strftime("%d %b")
        today_row = [row for row in series if row["label"] == today_label][0]
        self.assertEqual(today_row["total"], Decimal("250"))

    def test_revenue_by_month_sums(self):
        record_payment(self.order, "250", PaymentMode.CASH, self.user)
        series = services.revenue_by_month(months=6)
        self.assertEqual(len(series), 6)
        self.assertEqual(sum(row["total"] for row in series), Decimal("250"))

    def test_revenue_by_month_splits_across_boundary(self):
        # Payments (not orders) drive revenue: backdate two payments into
        # adjacent months at IST-safe midday hours.
        from datetime import datetime, time

        from billing.models import Payment

        today = timezone.localdate()
        first_this = today.replace(day=1)
        first_prev = (first_this - timezone.timedelta(days=1)).replace(day=1)

        p1 = record_payment(self.order, "100", PaymentMode.CASH, self.user)
        p2 = record_payment(self.order, "50", PaymentMode.CASH, self.user)
        Payment.objects.filter(id=p1.id).update(
            created_at=timezone.make_aware(datetime.combine(first_prev, time(12, 0)))
        )
        Payment.objects.filter(id=p2.id).update(
            created_at=timezone.make_aware(datetime.combine(first_this, time(12, 0)))
        )

        series = services.revenue_by_month(months=2)
        got = {row["label"]: row["total"] for row in series}
        self.assertEqual(got[f"{first_prev.year}-{first_prev.month:02d}"], Decimal("100"))
        self.assertEqual(got[f"{first_this.year}-{first_this.month:02d}"], Decimal("50"))

    def test_bar_width(self):
        self.assertEqual(services.bar_width(Decimal("50"), Decimal("100")), 50)
        self.assertEqual(services.bar_width(Decimal("0"), Decimal("0")), 0)


class ProfitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_profit_excludes_cancelled_and_blank_material(self):
        Order.objects.create(
            customer=self.customer, total=Decimal("1000.00"), material_cost=Decimal("300.00")
        )
        Order.objects.create(customer=self.customer, total=Decimal("500.00"))  # no material
        cancelled = Order.objects.create(
            customer=self.customer,
            total=Decimal("9000.00"),
            material_cost=Decimal("100.00"),
            stage=OrderStage.FITTING,
        )
        advance_stage(cancelled, OrderStage.CANCELLED, self.user, note="cancelled")
        result = services.profit_totals()
        self.assertEqual(result["revenue"], Decimal("1500.00"))
        self.assertEqual(result["material"], Decimal("300.00"))
        self.assertEqual(result["profit"], Decimal("1200.00"))


class PipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_orders_by_stage_covers_lifecycle(self):
        Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        rows = {row["stage"]: row["count"] for row in services.orders_by_stage()}
        self.assertEqual(rows[OrderStage.CUTTING], 1)
        self.assertEqual(rows[OrderStage.MEASUREMENT], 0)

    def test_pipeline_counts(self):
        Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        Order.objects.create(customer=self.customer, stage=OrderStage.DELIVERED)
        counts = services.pipeline_counts()
        self.assertEqual(counts, {"delivered": 1, "cancelled": 0, "open": 1, "total": 2})

    def test_service_mix(self):
        Order.objects.create(
            customer=self.customer, service_type="blouse", total=Decimal("500.00")
        )
        Order.objects.create(
            customer=self.customer, service_type="blouse", total=Decimal("700.00")
        )
        mix = {row["service"]: row for row in services.service_mix()}
        self.assertEqual(mix["blouse"]["count"], 2)
        self.assertEqual(mix["blouse"]["revenue"], Decimal("1200.00"))


class StuckOrdersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_fresh_order_not_stuck(self):
        Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        self.assertEqual(services.stuck_orders(), [])

    def test_delivered_never_stuck(self):
        Order.objects.create(customer=self.customer, stage=OrderStage.DELIVERED)
        self.assertEqual(services.stuck_orders(), [])

    def test_old_order_is_stuck(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        # Backdate creation beyond the stuck threshold.
        Order.objects.filter(id=order.id).update(
            created_at=timezone.now() - timezone.timedelta(days=5)
        )
        stuck = services.stuck_orders()
        self.assertEqual([o.id for o in stuck], [order.id])
        self.assertGreaterEqual(stuck[0].stuck_days, 5)

    def test_just_under_threshold_not_stuck(self):
        # Rule is strictly older-than-3-days. Exact equality is untestable
        # (the clock moves between backdating and the query), so assert a
        # minute inside the window is not stuck.
        order = Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        Order.objects.filter(id=order.id).update(
            created_at=timezone.now() - timezone.timedelta(days=3) + timezone.timedelta(minutes=1)
        )
        self.assertEqual(services.stuck_orders(), [])

    def test_just_over_threshold_is_stuck(self):
        order = Order.objects.create(customer=self.customer, stage=OrderStage.CUTTING)
        Order.objects.filter(id=order.id).update(
            created_at=timezone.now() - timezone.timedelta(days=3, minutes=1)
        )
        stuck = services.stuck_orders()
        self.assertEqual([o.id for o in stuck], [order.id])


class TopCustomersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mgr", password="secret123")

    def test_ranked_by_revenue(self):
        big = Customer.objects.create(name="Big", phone="111")
        small = Customer.objects.create(name="Small", phone="222")
        Order.objects.create(customer=big, total=Decimal("5000.00"))
        Order.objects.create(customer=small, total=Decimal("500.00"))
        top = services.top_customers(limit=5)
        self.assertEqual([c.name for c in top], ["Big", "Small"])
        self.assertEqual(top[0].revenue, Decimal("5000.00"))

    def test_customers_without_orders_excluded(self):
        Customer.objects.create(name="None", phone="333")
        self.assertEqual(services.top_customers(), [])


class DashboardViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="secret123")
        self.admin.profile.role = "admin"
        self.admin.profile.save()
        self.manager = User.objects.create_user(username="mgr", password="secret123")
        self.customer = Customer.objects.create(name="Priya", phone="9876543210")

    def test_dashboard_renders_with_data(self):
        order = Order.objects.create(customer=self.customer, total=Decimal("800.00"))
        record_payment(order, "300", PaymentMode.CASH, self.admin)
        self.client.login(username="adm", password="secret123")
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics")
        self.assertContains(response, "Stuck orders")
        self.assertContains(response, "Top customers")

    def test_dashboard_empty_state(self):
        self.client.login(username="adm", password="secret123")
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing stuck")

    def test_manager_forbidden(self):
        self.client.login(username="mgr", password="secret123")
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 403)
